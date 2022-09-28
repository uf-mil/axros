from __future__ import annotations

import asyncio
import functools
import os
import random
import socket
import sys
import time
import traceback
import warnings
from types import TracebackType
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

import aiohttp
import genpy
import yaml
from aiohttp import web
from aiohttp_xmlrpc import handler as xmlrpc_handler
from roscpp.srv import (
    GetLoggers,
    GetLoggersRequest,
    GetLoggersResponse,
    SetLoggerLevel,
    SetLoggerLevelRequest,
    SetLoggerLevelResponse,
)
from rosgraph_msgs.msg import Clock

from . import (
    exceptions,
    publisher,
    rosxmlrpc,
    service,
    serviceclient,
    subscriber,
    tcpros,
    types,
    util,
)

M = TypeVar("M", bound=types.Message)
Request = TypeVar("Request", bound=types.Message)
Reply = TypeVar("Reply", bound=types.Message)
S = TypeVar("S", bound=types.ServiceMessage)


class _XMLRPCSlave(xmlrpc_handler.XMLRPCView):
    """
    This class implements the ROS slave API for a node handle created with txros.
    Each method beginning with "rpc_" can be called by another process, such as
    another ROS node or ROS itself, through XMLRPC. The name following "rpc_" is
    the XMLRPC method name needed to execute the function.

    For more information on the ROS Slave API, please see http://wiki.ros.org/ROS/Slave_API.
    """

    # pylint: disable=invalid-name,no-self-use,missing-function-docstring
    #     invalid-name: method name must begin with rpc and use camel case
    #     no-self-use: this is okay; these methods must be a part of this class
    #                  to respond to incoming XMLRPC requests
    #     missing-function-docstring: below methods are documented in the Slave API
    #                                 linked above

    node_handle: NodeHandle
    background_tasks: set[asyncio.Future]

    def __init__(self, request: web.Request, *, node_handle: NodeHandle):
        self.node_handle = node_handle
        self.background_tasks = set()
        super().__init__(request)

    def __getattr__(self, attr: str):
        if attr.startswith("xmlrpc_") or attr.startswith("rpc_"):
            print(attr, "not implemented")
        raise AttributeError(attr)

    def rpc_getBusStats(self, _) -> tuple[int, str, list[list[Any]]]:
        return 1, "success", [[], [], []]  # XXX

    def rpc_getBusInfo(self, _) -> tuple[int, str, list[Any]]:
        # list of (connectionId, destinationId, direction, transport, topic, connected)
        return 1, "success", []  # XXX

    def rpc_getMasterUri(self, _) -> tuple[int, str, str]:
        return 1, "success", self.node_handle.master_uri

    async def rpc_shutdown(self, _, msg: str = ""):
        print(
            f"Shutdown of {self.node_handle.get_name()} node requested. Reason: {msg}"
        )
        try:
            await util.wrap_timeout(
                self.node_handle.shutdown(shutdown_servers=False), 3
            )
        except asyncio.TimeoutError:
            pass

        async def _kill_soon():
            await self.node_handle.shutdown_servers()
            print(f"Shut down of {self.node_handle.get_name()} node complete.")
            sys.exit(0)

        task = asyncio.create_task(_kill_soon())
        # Get exception so the loop doesn't print it out later when the program
        # has completed.
        task.add_done_callback(lambda t: t.exception())
        task.add_done_callback(self.background_tasks.remove)
        self.background_tasks.add(task)
        return (1, "success", False)

    def rpc_getPid(self, _) -> tuple[int, str, int]:
        return 1, "success", os.getpid()

    def rpc_getSubscriptions(self, _) -> tuple[int, str, list[Any]]:
        # This does not appear to be of importance - ROS appears to track this itself
        return 1, "success", []

    def rpc_getPublications(self, _) -> tuple[int, str, list[Any]]:
        # This does not appear to be of importance - ROS appears to track this itself
        return 1, "success", []

    def rpc_paramUpdate(self, parameter_key, parameter_value) -> tuple[int, str, bool]:
        # This behavior is not utilized by any txros-constructed nodes
        del parameter_key, parameter_value
        return 1, "success", False

    def rpc_publisherUpdate(self, _, topic: str, publishers):
        return self.node_handle.xmlrpc_handlers.get(
            ("publisherUpdate", topic), lambda _: (1, "success", True)
        )(publishers)

    def rpc_requestTopic(self, _, topic: str, protocols):
        return self.node_handle.xmlrpc_handlers.get(
            ("requestTopic", topic),
            lambda _: (-1, f"Not a publisher of [{topic}]", []),
        )(protocols)


class NodeHandle:
    """
    Class representing a Pythonic handle for a ROS node, similar to the C++
    concept of a node handle. This class provides asynchronous communication with
    several ROS features, including publishers, subscribers, and services through
    helper methods.

    This node must be constructeed using the `await` keyword because it spins up
    resources upon initialization. As a result, you cannot setup this node using a specialized
    method.

    You must shutdown the node using :meth:`~.shutdown`. Otherwise, you can use an
    asynchronous context manager.

    .. warning::

        You cannot spawn two nodes with the same name. Attempts to do so will result
        in the node which already exists being killed to make way for the new node
        you are initiailizing. If the other node is shutdown, it will be shutdown
        safely.

    .. container:: operations

        .. describe:: async with x:

            On enter, the node handle is setup through :meth:`~.setup`. Upon leaving
            the context block, the nodehandle is :meth:`~.shutdown`.

    Attributes:
        master_uri (str): The master URI used by the handle to communicate with ROS.
        shutdown_callbacks (set[Callable[[], Coroutine[Any, Any, Any]]]): The callbacks
            that are executed when the a shutdown is requested for the node. This
            allows the node to call the shutdown coroutines of other classes, such
            as :meth:`txros.Publisher.shutdown` and :meth:`txros.Subscriber.shutdown`.
        master_proxy (ROSMasterProxy): The proxy to the ROS Master URI. This is used
            by the node for all communication.
        xmlrpc_server_uri (str): The XMLRPC URI for the node.
        tcpros_handlers (dict[tuple[str, str], types.TCPROSProtocol]): The handlers
            for incoming TCPROS connections. This allows the node to route incoming
            connections to the appropriate class.
        xmlrpc_handlers (dict[tuple[str, str], Callable[[Any], tuple[int, str, Any]]]): The
            handlers for incoming XMLRPC connections. Acts similar to :attr:`~.tcpros_handlers`.
    """

    # pylint: disable=too-many-public-methods
    #     too-many-public-methods: while we could move methods out of this class,
    #                              that would reduce the simplicity of the class
    #                              as well as its similarity to ROS

    master_uri: str
    _ns: str
    _name: str
    _addr: str
    shutdown_callbacks: set[Callable[[], Coroutine[Any, Any, Any]]]
    _aiohttp_session: aiohttp.ClientSession
    _tcpros_server_uri: str
    _is_running: bool
    master_proxy: rosxmlrpc.ROSMasterProxy
    xmlrpc_server_uri: str
    tcpros_handlers: dict[tuple[str, str], types.TCPROSProtocol]
    xmlrpc_handlers: dict[tuple[str, str], Callable[[Any], tuple[int, str, Any]]]
    _tcpros_server: asyncio.Server
    _tcpros_server_addr: tuple[str, int]
    _use_sim_time: bool
    _clock_sub: subscriber.Subscriber
    _remappings: dict[str, str]
    _sim_time: genpy.Time
    _xmlrpc_runner: web.AppRunner
    _xmlrpc_site: web.TCPSite
    get_log_serv: service.Service
    set_log_serv: service.Service

    @classmethod
    def from_argv_with_remaining(
        cls,
        default_name: str,
        argv: list[str] | None = None,
        anonymous: bool = False,
        *,
        always_default_name=False,
    ) -> tuple[NodeHandle, list[str]]:
        """
        Constructs a handle from an argument vector. Attempts to find any missing
        values for node constructionf from environment variables and the argument
        vector.

        Args:
            default_name (str): The default name of the node.
            argv (list[str] | None): The argument vector to use.
            anonymous (bool): Whether to make the node anonymous. Appends a random
                number to the end of the node name.
            always_default_name (bool): Whether to always use the default name of
                the node, avoiding remappings. Commonly used in testing or complex
                environments where stronger node control is desired. Defaults to ``True``.
        """
        if not argv:
            argv = sys.argv

        res = [argv[0]]
        remappings = {}
        for arg in argv[1:]:
            if ":=" in arg:
                before, after = arg.split(":=")
                assert before not in remappings
                remappings[before] = after
            else:
                res.append(arg)

        name = default_name
        if anonymous:
            name = f"{name}_{random.randrange(2**62):016x}"
        if "__name" in remappings and not always_default_name:
            name = remappings["__name"]

        addr = socket.gethostname()  # could be a bit smarter
        addr = os.environ.get("ROS_IP", addr)
        addr = os.environ.get("ROS_HOSTNAME", addr)
        addr = remappings.get("__ip", addr)
        addr = remappings.get("__hostname", addr)

        master_uri = None
        master_uri = os.environ.get("ROS_MASTER_URI", master_uri)
        master_uri = remappings.get("__master", master_uri)
        if master_uri is None:
            raise ValueError(
                "Either ROS_MASTER_URI variable or __master has to "
                "be provided for node to connect to ROS."
            )

        namespace = ""
        namespace = os.environ.get("ROS_NAMESPACE", namespace)
        namespace = remappings.get("__ns", namespace)

        return (
            cls(
                namespace=namespace,
                name=name,
                addr=addr,
                master_uri=master_uri,
                remappings=remappings,
            )
        ), res

    @classmethod
    def from_argv(cls, *args, **kwargs) -> NodeHandle:
        """
        Equivalent to :meth:`~.from_argv_with_remaining`.
        """
        return (cls.from_argv_with_remaining(*args, **kwargs))[0]

    def __init__(
        self,
        namespace: str,
        name: str,
        addr: str,
        master_uri: str,
        remappings: dict[str, str],
    ):
        """
        Args:
            namespace (str): The namespace to put the node under. The namespace should
                not end with a `/`. For example, `nsexample/test`.
            name (str): The name of the node itself. The name will be appended behind
                the namespace.
            addr (str): The address of the ROS master server, typically specified
                as an IPv4 address, and often, ``localhost``.
            master_uri (str): The URI linking to the ROS master server.
            remappings (dict[str, str]): Any remappings to use in the :meth:`~.setup` method.
        """
        self._ns = namespace
        self._name = name
        self._addr = addr
        self.master_uri = master_uri
        self._remappings = remappings
        self._is_running = False
        self._is_setting_up = False

    def __str__(self) -> str:
        return (
            f"<txros.NodeHandle at 0x{id(self):0x}, "
            f"name={self._name} "
            f"running={self.is_running()} "
            f"tcpros_uri={getattr(self, '_tcpros_server_uri', None)} "
            f"xmlrpc_uri={getattr(self, 'xmlrpc_server_uri', None)}>"
        )

    async def setup(self):
        """
        Sets up the node handle. This must be called before the node is used.

        Raises:
            AssertionError: A condition was not met with the variables supplied
                to the node in construction.
        """
        if self._ns:
            assert self._ns[0] == "/"
        assert not self._ns.endswith("/")
        self._ns = self._ns  # valid values: '', '/a', '/a/b'

        assert "/" not in self._name
        self._name = self._ns + "/" + self._name

        self.shutdown_callbacks = set()

        self._addr = self._addr
        self._is_setting_up = True

        self._aiohttp_session = aiohttp.ClientSession()
        self.master_proxy = rosxmlrpc.ROSMasterProxy(
            rosxmlrpc.AsyncServerProxy(self.master_uri, self),
            self._name,
        )

        self.xmlrpc_handlers = {}

        # Start up an HTTP server to handle incoming XMLRPC requests - below is
        # an alternative to the blocking web.run_app method that is sometimes
        # used with aiohttp.
        app = web.Application()

        async def handle_xmlrpc(request: web.Request):
            slave = _XMLRPCSlave(request, node_handle=self)
            return await slave.post()

        app.router.add_route("*", "/", handle_xmlrpc)

        self._xmlrpc_runner = web.AppRunner(app)
        await self._xmlrpc_runner.setup()
        self._xmlrpc_site = web.TCPSite(self._xmlrpc_runner, host=self._addr, port=0)
        await self._xmlrpc_site.start()

        # self.shutdown_callbacks.add(self._xmlrpc_runner.cleanup)
        self.xmlrpc_server_uri = "http://%s:%i/" % (
            self._addr,
            self._xmlrpc_site._server.sockets[0].getsockname()[1],
        )

        self.tcpros_handlers = {}
        self._tcpros_server = await asyncio.start_server(
            functools.partial(tcpros.callback, self.tcpros_handlers),
            family=socket.AF_INET,
            host=self._addr,
            port=0,
        )  # Port 0 lets the host assign the port for us
        tcpros_server_port = self._tcpros_server.sockets[0].getsockname()[1]
        self._tcpros_server_addr = self._addr, tcpros_server_port
        self._tcpros_server_uri = f"rosrpc://{self._addr}:{tcpros_server_port}"

        while True:
            try:
                other_node_uri = await self.master_proxy.lookupNode(self._name)
            except rosxmlrpc.ROSMasterError:
                break  # assume that error means unknown node
            except Exception as _:
                traceback.print_exc()
                await util.wall_sleep(1)  # pause so we don't retry immediately
            else:
                other_node_proxy = rosxmlrpc.AsyncServerProxy(other_node_uri, self)
                other_node_proxy = rosxmlrpc.ROSMasterProxy(
                    other_node_proxy, self._name
                )
                try:
                    await util.wrap_timeout(
                        other_node_proxy.shutdown("new node registered with same name"),
                        3,
                    )
                except ConnectionRefusedError:
                    pass
                except Exception:
                    traceback.print_exc()
                break

        try:
            self._use_sim_time = await self.get_param("/use_sim_time")
        except rosxmlrpc.ROSMasterError:  # assume that error means not found
            self._use_sim_time = False

        if self._use_sim_time:

            def got_clock(msg: Clock):
                self._sim_time = msg.clock

            self._clock_sub = self.subscribe("/clock", Clock, got_clock)
            await self._clock_sub.setup()
            # make sure self._sim_time gets set before we continue
            await util.wrap_time_notice(
                self._clock_sub.get_next_message(),
                1,
                "getting simulated time from /clock",
            )

        for k, v in self._remappings.items():
            if k.startswith("_") and not k.startswith("__"):
                await self.set_param(self.resolve_name("~" + k[1:]), yaml.safe_load(v))

        async def get_loggers_response(_: GetLoggersRequest) -> GetLoggersResponse:
            return GetLoggersResponse()

        async def set_loggers_response(
            _: SetLoggerLevelRequest,
        ) -> SetLoggerLevelResponse:
            return SetLoggerLevelResponse()

        self.get_log_serv = self.advertise_service(
            "~get_loggers", GetLoggers, get_loggers_response
        )
        self.set_log_serv = self.advertise_service(
            "~set_logger_level", SetLoggerLevel, set_loggers_response
        )
        await asyncio.gather(self.get_log_serv.setup(), self.set_log_serv.setup())
        self.shutdown_callbacks.update(
            [self.get_log_serv.shutdown, self.set_log_serv.shutdown]
        )

        self._is_running = True
        self._is_setting_up = False
        return self

    async def __aenter__(self) -> NodeHandle:
        await self.setup()
        return self

    async def __aexit__(
        self, exc_type: type[Exception], exc_value: Exception, traceback: TracebackType
    ):
        await self.shutdown()

    def __del__(self):
        if self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"shutdown() was never called for node '{self._name}'",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)

    async def shutdown(self, *, shutdown_servers: bool = True) -> None:
        """
        Shuts down all active connections. This should always be called before
        the loop is closed, or else multiple instances of :class:`ResourceWarning`
        will be emitted.
        """
        if not self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The {self._name} node is not currently running. It may have been shutdown previously or never started.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)
            return

        if self._is_running:
            return await self._real_shutdown(shutdown_servers)

    async def shutdown_servers(self):
        if not self._aiohttp_session.closed:
            self._tcpros_server.close()
            await self._tcpros_server.wait_closed()
            await self._xmlrpc_runner.cleanup()
            await self._aiohttp_session.close()

    async def _real_shutdown(self, shutdown_servers: bool):
        self._is_running = False

        count, total_coros = 1, len(self.shutdown_callbacks)
        while self.shutdown_callbacks:
            self.shutdown_callbacks, old = set(), self.shutdown_callbacks
            old_coros = [func() for func in old]
            for coro in old_coros:
                try:
                    await coro
                    count += 1
                except:
                    traceback.print_exc()

        # Close the open aiohttp connection used for XMLRPC
        # Clean this up LAST - other services/publishers/subscribers may still
        # be using this session
        if shutdown_servers:
            await self.shutdown_servers()

    def get_name(self) -> str:
        """
        Returns:
            str: The name of the node handle.
        """
        return self._name

    def get_time(self) -> genpy.Time:
        """
        Returns the time used by the node handle. If simulated time is requested
        to be used, then the current simulated time is returned. Otherwise, the current
        real time is returned.

        Returns:
            genpy.Time: The current time used by the node handle.
        """
        if self._use_sim_time:
            return self._sim_time
        else:
            return genpy.Time(time.time())

    async def sleep_until(self, future_time: float | int | genpy.Time) -> None:
        """
        Sleeps for a specified amount of time.

        Args:
            future_time (Union[float, int, genpy.Time]): The amount of time to sleep until.
                If a float or integer is used, then the generated time is constructed
                through the ``from_sec`` method of ``genpy.Time``.

        Raises:
            TypeError: The type of argument passed to ``future_time`` is not an acceptable
                type.
        """
        if isinstance(future_time, (float, int)):
            future_time = genpy.Time.from_sec(future_time)
        elif not isinstance(future_time, genpy.Time):
            raise TypeError("expected float or genpy.Time")

        if self._use_sim_time:
            while self._sim_time < future_time:
                await self._clock_sub.get_next_message()
        else:
            while True:
                current_time = self.get_time()
                if current_time >= future_time:
                    return
                await util.wall_sleep((future_time - current_time).to_sec())

    async def sleep(self, duration: float | int | genpy.Duration) -> None:
        """
        Sleeps for a specified duration of time.

        Args:
            duration (Union[float, int, genpy.Duration]): The amount of time to sleep
                for.

        Raises:
            TypeError: The argument passed to ``duration`` was not an allowed type.
            ValueError: The request sleep length was non-negative.
        """
        if isinstance(duration, (float, int)):
            if duration < 0:
                raise ValueError("The sleep length must be non-negative.")
            duration = genpy.Duration.from_sec(duration)
        elif not isinstance(duration, genpy.Duration):
            raise TypeError("expected float or genpy.Duration")

        return await self.sleep_until(self.get_time() + duration)

    def resolve_name_without_remapping(self, name: str) -> str:
        """
        Resolves the true name of a given node path.

        Args:
            name (str): The path to resolve.

        Returns:
            str: The resolved path.
        """
        if name.startswith("/"):
            return name
        if name.startswith("~"):
            return self._name + "/" + name[1:]
        return self._ns + "/" + name

    def resolve_name(self, name: str) -> str:
        """
        Resolves the name of the node handle and updates the name attribute with this
        resolved name.
        """
        name = self.resolve_name_without_remapping(name)
        for before_unresolved, after_unresolved in self._remappings.items():
            before = self.resolve_name_without_remapping(before_unresolved)
            after = self.resolve_name_without_remapping(after_unresolved)
            if name == before or name.startswith(before + "/"):
                return after + name[len(before) :]
        return name

    def is_running(self) -> bool:
        """
        Returns:
            bool: Whether the node handle is running. This is ``True`` after the
            node is :meth:`~.setup` and becomes ``False`` when the node is :meth:`~.shutdown`.
            Will always be opposite of :meth:`~.is_shutdown`.
        """
        return self._is_running

    def is_shutdown(self) -> bool:
        """
        Returns:
            bool: Whether the node handle is not running. This is ``True`` by default,
            calling :meth:`~.setup` on the node will make this ``False``. Will always
            be opposite of :meth:`~.is_running`.
        """
        return not self._is_running

    def advertise_service(
        self,
        name: str,
        service_message: types.ServiceMessage[Request, Reply],
        callback: Callable[[Request], Awaitable[Reply]],
    ) -> service.Service[Request, Reply]:
        """
        Creates a service using this node handle. The arguments and keyword
        arguments passed to this method are passed into the constructor of the service;
        check there for information on what arguments can be passed in.

        Args:
            name (str): The name to use for the service.
            service_message (type[genpy.Message]): A ROS service class to use with the service.
                The callback method used by the class will receive the request
                class associated with the service, and is expected to return the
                response class associated with this class.
            callback (Callable[[genpy.Message], Awaitable[genpy.Message]]): An asynchronous callback
                to process all incoming service requests. The returned message type
                should be the reply type associated with the service.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            txros.Service: The given service.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return service.Service(self, name, service_message, callback)

    def get_service_client(
        self, name: str, service_type: S
    ) -> serviceclient.ServiceClient[S]:
        """
        Creates a service client using this node handle. This class can interface
        with services by calling them with a particular request message and receiving
        a response message.

        This class is a generic and supports being associated with the service message
        used by the client and service in conjunction.

        Args:
            name (str): The name of the service client.
            service_type (type[genpy.Message]): The message representing the service. This
                should not be the request or response message class, but should be
                the main message class.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            txros.ServiceClient: The constructed service client.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return serviceclient.ServiceClient(self, name, service_type)

    def subscribe(
        self,
        name: str,
        message_type: type[M],
        callback: Callable[[M], M | None] = lambda _: None,
    ) -> subscriber.Subscriber[M]:
        """
        Creates a subscriber using this node handle. The arguments and keyword
        arguments passed to this method are passed into the constructor of the subscriber;
        check there for information on what arguments can be passed in.

        Note that the subscriber can only be used when it is setup through :meth:`Subscriber.setup`.

        Args:
            name (str): The name of the topic to subscribe to.
            message_type (type[genpy.Message]): The message class shared by the topic.
            callback (Callable[[genpy.Message], genpy.Message | None]): The callback to use with
                the subscriber. The callback should receive an instance of the message
                shared by the topic and return None.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            txros.Subscriber: The constructed subscriber.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return subscriber.Subscriber(self, name, message_type, callback)

    def advertise(
        self, name: str, message_type: type[M], *, latching: bool = False
    ) -> publisher.Publisher[M]:
        """
        Creates a publisher using this node handle. The arguments and keyword
        arguments passed to this method are passed into the constructor of the publisher;
        check there for information on what arguments should be passed in.

        Note that the publisher can only be used when it is setup through :meth:`Publisher.setup`.

        Args:
            name (str): The name of the topic to publish.
            message_type (Type[genpy.Message]): The message type that will be published
                on the topic.
            latching (bool): Enables latching on the publisher. This ensures that all
                new connections are immediately sent the most recently sent message
                when they connect to the publisher.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            txros.Publisher: The given publisher.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return publisher.Publisher(self, name, message_type, latching)

    async def get_param(self, key: str) -> rosxmlrpc.XMLRPCLegalType:
        """
        Gets a parameter value from the ROS parameter server. If the key requested
        is a namespace, then a dictionary representing all keys below the given
        namespace is returned.

        Args:
            key (str): The name of the parameter to retrieve from the server.

        Raises:
            txros.ROSMasterException: The parameter is not set.
            NotSetup: The node is not running.

        Returns:
            :class:`txros.XMLRPCLegalType`: The value of the parameter with the given
            name.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.getParam(key)

    async def has_param(self, key: str) -> bool:
        """
        Checks whether the ROS parameter server has a parameter with the specified
        name.

        Args:
            key (str): The name of the parameter to check the status of.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            bool: Whether the parameter server has the specified key.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.hasParam(key)

    async def delete_param(self, key: str) -> int:
        """
        Deletes the specified parameter from the parameter server.

        Args:
            key (str): The parameter to delete from the parameter server.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            int: The result of the delete operation. According to ROS documentation,
            this value can be ignored.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.deleteParam(key)

    async def set_param(self, key: str, value: Any) -> int:
        """
        Sets a parameter and value in the ROS parameter server.

        Args:
            key (str): The parameter to set in the parameter server.
            value (:class:`txros.XMLRPCLegalType`): The value to set for the given parameter.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            int: The result of setting the parameter. According to the ROS documentation,
            this value can be ignored.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.setParam(key, value)

    async def search_param(self, key: str) -> str:
        """
        Searches for a parameter on the ROS parameter server, and returns the first
        key found that matches the search term.

        For more information on how searching is done to find a matching key, view
        the `ROS wiki documentation on this method <http://wiki.ros.org/ROS/Parameter%20Server%20API>`_.

        Args:
            key (str): The search key to use to find parameters.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            str: The name of the first key found.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.searchParam(key)

    async def get_param_names(self) -> list[str]:
        """
        Gets the names of all parameters in the ROS parameter server.

        Raises:
            NotSetup: The node is not running. The node likely needs to be :meth:`~.setup`.

        Returns:
            List[str]: The names of all parameters in the server.
        """
        if not self._is_running and not self._is_setting_up:
            raise exceptions.NotSetup(self, self)

        return await self.master_proxy.getParamNames()
