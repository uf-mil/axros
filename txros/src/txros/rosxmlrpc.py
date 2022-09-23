"""
Asynchronous helper classes and methods to provide stable communication over
XMLRPC.

These classes are primarily used to implement custom handlers into the TCPROS
system provided by ROS.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Iterable, Protocol, Union
from xmlrpc import client

from lxml.etree import XMLParser, fromstring, tostring

from .exceptions import TxrosException

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

XMLRPCLegalType = Union[bool, int, float, str, list, tuple, dict, None]


class CallbackUnlimited(Protocol):
    def __call__(self, *args: Any) -> Coroutine[Any, Any, Any]:
        ...


class XMLRPCMethod:
    """
    Helper class to represent a specific method called on an XMLRPC server.
    """

    def __init__(self, send: Callable, name: str):
        self.send = send
        self.name = name

    def __getattr__(self, name: str):
        return XMLRPCMethod(self.send, f"{self.name}.{name}")

    async def __call__(self, *args):
        ret = await self.send(self.name, args)
        return ret


class AsyncioTransport(client.Transport):
    """
    Transport class used by :class:`AsyncServerProxy` used to implement an XMLRPC-style
    communication framework over HTTP.
    """

    def __init__(self, node_handle: NodeHandle):
        super().__init__(use_datetime=False, use_builtin_types=False)
        self.session = node_handle._aiohttp_session
        self.node_handle = node_handle

    async def request(self, url: str, request_body: bytes):
        body = ""
        try:
            response = await self.session.post(
                url, data=request_body, headers={"Content-Type": "application/xml"}
            )
            body = await response.text()
            if response.status != 200:
                print(dict(response.headers))
                raise client.ProtocolError(
                    url,
                    errcode=response.status,
                    errmsg=body,
                    headers={str(k): v for k, v in dict(response.headers).items()},
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            raise XMLRPCException(e, request_body, self.node_handle) from e

        return self.parse_response(body)

    def parse_response(self, response: str):
        parser, unmarshaller = self.getparser()
        parser.feed(response)
        parser.close()
        return unmarshaller.close()


class AsyncServerProxy(client.ServerProxy):
    """
    Asynchronous server proxy to a specific server URI using :class:`aiohttp.ClientSession`.
    This class is used in conjunction with :class:`txros.ROSMasterProxy`.

    Attributes:
        transport (AsyncioTransport): The transports used to facilitate requests.
        session (aiohttp.ClientSession): The client session used in the communication.
        uri (str): The URI used to connect to ROS master.
    """

    def __init__(
        self,
        uri: str,
        node_handle: NodeHandle,
        *,
        headers: Iterable[tuple[str, str]] | None = None,
        context: Any | None = None,
    ) -> None:
        """
        Args:
            uri (str): The URI used to connect to ROS Master. This can often be
                obtained through ``os.environ["ROS_MASTER_URI"]``.
            node_handle (txros.NodeHandle): The node handle starting the asynchronous
                proxy.
            headers (Iterable[tuple[:class:`str`, :class:`str`]]): The specific headers
                to use for connection.
            context (Any | None): The context for the asynchronous session.
        """
        self.transport = AsyncioTransport(node_handle)
        self.session = node_handle._aiohttp_session
        self.node_handle = node_handle
        self.uri = uri

        if not headers:
            headers = [
                ("User-Agent", "python-mil/asyncioxmlrpc"),
                ("Accept", "text/xml"),
                ("Content-Type", "text/xml"),
            ]

        super().__init__(
            uri,
            self.transport,
            encoding=None,
            verbose=False,
            allow_none=True,
            use_datetime=True,
            use_builtin_types=True,
            headers=headers,
            context=context,
        )

    async def __request(self, methodname, params):
        # For debugging purposes, uncommenting this line may prove to be helpful.
        # print(
        #     f"Requesting method {methodname} with params {params} (client closed: {self.transport.session.closed})"
        # )
        request = client.dumps(
            params, methodname, encoding=None, allow_none=True
        ).encode()

        response = await self.transport.request(self.uri, request)

        if len(response) == 1:
            response = response[0]

        return response

    def __getattr__(self, name: str):
        return XMLRPCMethod(self.__request, name)


class XMLRPCException(TxrosException):
    """
    Represents an error that occurred in the XMLRPC communication. Inherits
    from :class:`~.TxrosException`.

    .. container:: operations

        .. describe:: str(x)

            Pretty-prints the error status code and message.

        .. describe:: repr(x)

            Pretty-prints the error status code and message. Equivalent to
            ``str(x)``.

    Attributes:
        exception (Exception): The exception caught in the error.
        request_body (bytes): The request body of the request which caused the exception.
    """

    def __init__(
        self, exception: Exception, request_body: bytes, node_handle: NodeHandle
    ):
        self.exception = exception
        self.request_body = request_body
        self.node_handle = node_handle
        method_name = None
        try:
            parser = XMLParser(resolve_entities=True)
            root = fromstring(self.request_body)
            method_name = root.xpath("//methodName[1]/text()")[0]
        except:
            # If some exception is preventing parsing, don't worry about it
            pass

        help_message = ""
        if (
            exception.__class__.__name__ == "ClientConnectorError"
            and method_name == "requestTopic"
        ):
            help_message = f"It appears the node may not be able to find a node publishing the topic it is attempting to listen to. This likely means that the node publishing to the topic has died. Please check the publisher to the topic for issues and restart ROS.\n\nRequest body: {self.request_body}"

        super().__init__(
            f"An error occurred in the XMLRPC communication. The '{self.node_handle._name}' node attempted to call '{method_name}' resulting in an error. For help resolving this exception, please see the txros documentation."
            + (f"\n\n{help_message}" if help_message else ""),
            self.node_handle,
        )


class ROSMasterError(TxrosException):
    """
    Represents an exception that occurred when attempting to communicate with the
    ROS Master Server. Unlike :class:`~.ROSMasterFailure`, this indicates that the
    request completed, but the returned value is not usable. Similar to a bad request
    HTTP error.

    Inherits from :class:`~.TxrosException`.

    Attributes:
        ros_message (:class:`str`): The message from ROS explaining the exception.
    """

    def __init__(self, message: str, code: int, node_handle: NodeHandle):
        self.ros_message = message
        self._code = code  # Should always be -1
        super().__init__(f"Request from {node_handle._name}: {message}", node_handle)


class ROSMasterFailure(TxrosException):
    """
    Represents a failure that occurred after a node sent an outgoing XMLRPC request
    to ROS Master. Unlike :class:`~.ROSMasterError`, this indicates that the request
    failed in progress, and did not complete. This may cause side effects visible
    in other ROS services.

    Inherits from :class:`~.TxrosException`.

    Attributes:
        ros_message (:class:`str`): The message from ROS explaining the exception.
    """

    def __init__(self, message: str, code: int, node_handle: NodeHandle):
        self.ros_message = message
        self._code = code  # Should always be 0
        super().__init__(
            f"Request from {node_handle._name} caused ROS failure: {message}. Please consider restarting ROS.",
            node_handle,
        )


class ROSMasterProxy:
    """
    The txROS proxy to the ROS master server. This class does not provide a general
    suite of discrete methods for contracting with the master server, rather all
    method requests are forwarded to the server through the class' ``__getattr__``
    method.

    This class implements all operations related to communication with ROS through
    its [Master API](https://wiki.ros.org/ROS/Master_API). Generally, this class
    is used on behalf of a node or process to make outbound requests to other ROS services.
    It is not used to receive connections from other ROS services.

    Generally, this method should not be used in application-level code, rather helper
    methods should be called from classes such as :class:`txros.NodeHandle` or
    :class:`txros.Subscriber`. However, this class may be extended for new applications
    of low-level code.

    .. code-block:: python

        >>> import os
        >>> session = aiohttp.ClientSession()
        >>> proxy = AsyncServerProxy(os.environ["ROS_MASTER_URI"], session)
        >>> master = ROSMasterProxy(proxy, "")
        >>> print(await master.hasParam("/always_true"))
        True
        >>> await session.close()

    Args:
        proxy (:class:`AsyncServerProxy`): The proxy to a general XMLRPC server.
        caller_id (:class:`str`): The caller ID making the following requests.

    .. container:: operations

        .. describe:: x.attr

            Calls the master server with the attribute name representing the name
            of the method to call. The value is returned in a Deferred object.
            If the status code is not 1, then :class:`txros.Error` is raised.

            Raises:
                :class:`XMLRPCException`: General error in communication. This could
                    be due to a multitude of reasons.
                :class:`ROSMasterError`: The status code returned by the ROS master
                    server was -1, indicating that the request completed, but the associated
                    return value is not usable.
                :class:`ROSMasterFailure`: The status code returned by the ROS master
                    server was 0, indicating that the request did not successfully complete.
    """

    def __init__(self, proxy: AsyncServerProxy, caller_id: str):
        """
        Args:
            proxy (txros.AsyncServerProxy): The proxy representing an XMLRPC connection to the ROS
                master server.
            caller_id (str): The ID of the caller in the proxy.
        """
        self._master_proxy = proxy
        self._caller_id = caller_id

    def make_remote_caller(self, name: str) -> CallbackUnlimited:
        async def remote_caller(*args):
            status_code, status_message, value = await getattr(
                self._master_proxy, name
            )(self._caller_id, *args)
            if status_code == 1:  # SUCCESS
                return value
            if status_code == -1:
                raise ROSMasterError(
                    status_message, status_code, self._master_proxy.node_handle
                )
            raise ROSMasterFailure(
                status_message, status_code, self._master_proxy.node_handle
            )

        return remote_caller

    def __getattr__(self, name: str) -> CallbackUnlimited:
        return self.make_remote_caller(name)

    def request_topic(
        self, name: str, protocols: list[list[str]]
    ) -> Coroutine[Any, Any, list[XMLRPCLegalType]]:
        return self.make_remote_caller("requestTopic")(name, protocols)

    def lookup_service(self, service_name: str) -> Coroutine[Any, Any, str]:
        return self.make_remote_caller("lookupService")(service_name)

    def unregister_service(
        self, service: str, service_api: str
    ) -> Coroutine[Any, Any, int]:
        return self.make_remote_caller("unregisterService")(service, service_api)

    def register_service(
        self, service: str, service_api: str, caller_api: str
    ) -> Coroutine[Any, Any, int]:
        return self.make_remote_caller("registerService")(
            service, service_api, caller_api
        )

    def register_subscriber(
        self, topic: str, topic_type: str, caller_api: str
    ) -> Coroutine[Any, Any, list[str]]:
        return self.make_remote_caller("registerSubscriber")(
            topic, topic_type, caller_api
        )

    def register_publisher(
        self, topic: str, topic_type: str, caller_api: str
    ) -> Coroutine[Any, Any, list[str]]:
        return self.make_remote_caller("registerPublisher")(
            topic, topic_type, caller_api
        )

    def unregister_publisher(
        self, topic: str, caller_api: str
    ) -> Coroutine[Any, Any, list[str]]:
        return self.make_remote_caller("unregisterPublisher")(topic, caller_api)
