from __future__ import annotations

import asyncio
import time
import traceback
import warnings
from types import TracebackType
from typing import TYPE_CHECKING, Callable, Generic, TypeVar

import genpy

from . import exceptions, rosxmlrpc, tcpros, types, util

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

M = TypeVar("M", bound=types.Message)


class Subscriber(Generic[M]):
    """
    A subscriber in the txROS suite. This class should usually be made through
    :meth:`txros.NodeHandle.subscribe`.

    .. container:: operations

        .. describe:: async with x:

            On entering the block, the subscriber is :meth:`~.setup`; upon leaving the block,
            the subscriber is :meth:`~.shutdown`.
    """

    _message_futs: list[asyncio.Future]
    _publisher_threads: dict[str, asyncio.Task]
    _is_running: bool

    def __init__(
        self,
        node_handle: NodeHandle,
        name: str,
        message_type: type[M],
        callback: Callable[[M], M | None] = lambda message: None,
    ):
        """
        Args:
            node_handle (NodeHandle): The node handle used to communicate with the
                ROS master server.
            name (str): The name of the topic to subscribe to.
            message_type (Type[genpy.Message]): The message class shared by the topic.
            callback (Callable[[genpy.Message], genpy.Message | None]): The callback to use with
                the subscriber. The callback should receive an instance of the message
                shared by the topic and return None.

        .. note::

            If you are subscribing to a publisher publishing messages at a very
            fast rate, you should always use a callback over :meth:`~.get_next_message`.
        """
        self._node_handle = node_handle
        self._name = self._node_handle.resolve_name(name)
        self.message_type = message_type
        self._callback = callback

        self._publisher_threads = {}
        self._last_message = None
        self._last_message_time = None
        self._message_futs = []
        self._connections = {}

        self._node_handle.shutdown_callbacks.add(self.shutdown)
        self._is_running = False
        self._is_reading = {}

    def __str__(self) -> str:
        return (
            f"<txros.Subscriber at 0x{id(self):0x}, "
            f"name={self._name} "
            f"running={self.is_running()} "
            f"message_type={self.message_type} "
            f"node_handle={self._node_handle}>"
        )

    __repr__ = __str__

    async def setup(self) -> None:
        """
        Sets up the subscriber by registering the subscriber with ROS and listing
        handlers supported by the topic.

        The subscriber must be setup before use.
        """
        if self.is_running():
            raise exceptions.AlreadySetup(self, self._node_handle)

        if ("publisherUpdate", self._name) in self._node_handle.xmlrpc_handlers:
            self._node_handle.xmlrpc_handlers[("publisherUpdate", self._name)].append(
                self._handle_publisher_list
            )
        else:
            self._node_handle.xmlrpc_handlers[("publisherUpdate", self._name)] = [
                self._handle_publisher_list
            ]

        publishers = await self._node_handle.master_proxy.register_subscriber(
            self._name,
            self.message_type._type,
            self._node_handle.xmlrpc_server_uri,
        )
        self._handle_publisher_list(publishers)
        self._is_running = True

    def __del__(self):
        if self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The '{self._name}' subscriber was never shutdown(). This may cause issues with this instance of ROS - please fix the errors and completely restart ROS.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)

    async def __aenter__(self) -> Subscriber:
        await self.setup()
        return self

    async def __aexit__(
        self, exc_type: type[Exception], exc_value: Exception, traceback: TracebackType
    ):
        await self.shutdown()

    def is_running(self) -> bool:
        """
        Returns:
            bool: Whether the subscriber is running.
        """
        return self._is_running

    async def shutdown(self):
        """
        Shuts the subscriber down. All operations scheduled by the subscriber
        are immediately cancelled.

        Raises:
            ResourceWarning: The subscriber is already not running.
        """
        if not self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The {self._name} subscriber is not currently running. It may have been shutdown previously or never started.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)
            return

        for _, v in self._publisher_threads.items():
            v.cancel()

        try:
            await self._node_handle.master_proxy.unregisterSubscriber(
                self._name, self._node_handle.xmlrpc_server_uri
            )
        except Exception:
            traceback.print_exc()

        handlers = self._node_handle.xmlrpc_handlers["publisherUpdate", self._name]
        handlers.remove(self._handle_publisher_list)
        if not handlers:
            del self._node_handle.xmlrpc_handlers["publisherUpdate", self._name]

        self._node_handle.shutdown_callbacks.discard(self.shutdown)
        for _, task in self._publisher_threads.items():
            task.cancel()
        self._handle_publisher_list([])
        self._is_running = False

    def get_last_message(self) -> M | None:
        """
        Gets the last received message. This may be ``None`` if no message has been
        received.

        Returns:
            Optional[genpy.Message]: The last sent message, or ``None``.
        """
        return self._last_message

    def get_last_message_time(self) -> genpy.Time | None:
        """
        Gets the time associated with the last received message. May be ``None`` if
        no message has been received yet.

        Returns:
            Optional[genpy.Time]: The time of the last received message, or ``None``.
        """
        return self._last_message_time

    def get_next_message(self) -> asyncio.Future[M]:
        """
        Returns a :class:`~asyncio.Future` which will contain the next message
        received by the subscriber.

        .. warning::

            This method should not be used to reliably obtain messages from publishers
            publishing at very high rates. Because of the overhead in setting up
            futures, this method will likely cause some messages to get lost.

            Instead, you should use a callback with the subscriber - all messages
            are passed through the callback.

        Raises:
            NotSetup: The subscriber was never :meth:`~.setup` or was previously
                :meth:`~.shutdown`.

        Returns:
            asyncio.Future[genpy.Message]: A future which will eventually contain
            the next message published on the topic.
        """
        if not self.is_running():
            raise exceptions.NotSetup(self, self._node_handle)

        res = asyncio.Future()
        self._message_futs.append(res)
        return res

    def get_connections(self) -> list[str]:
        """
        Returns the ``callerid`` of each connected client. If a connection does
        not provide an ID, then the value may be None.

        Returns:
            List[str]: A list of the caller IDs of all nodes connected to the
            subscriber.
        """
        return list(self._connections.values())

    def recently_read(self, *, seconds=1) -> bool:
        """
        Whether the subscriber has recently read messages.

        Using this method is helpful when the subscriber may not be fast enough
        to keep up with all messages published. This method can be used to determine
        when the subscriber has finished reading queued messages. This is helpful
        at the end of programs, when a publisher has stopped publishing, but the
        subscriber has not yet received all messages.

        .. code-block:: python3

            >>> while sub.recently_read():
            ...     print("Subscriber is still reading messages...")
            ...     await asyncio.sleep(0.5)
            >>> print("Subscriber has finished catching up on messages!")

        Args:
            seconds (float): The number of seconds to wait after receiving a message
                before claiming that the subscriber is no longer actively reading messages.
                Defaults to 1 second.

        Returns:
            bool: Whether the subscriber has recently read messages.
        """
        last_message_time = self.get_last_message_time()
        if last_message_time is not None:
            return time.time() - last_message_time.to_sec() < seconds
        return False

    async def _publisher_thread(self, url: str) -> None:
        while True:
            try:
                proxy = rosxmlrpc.ROSMasterProxy(
                    rosxmlrpc.AsyncServerProxy(url, self._node_handle),
                    self._node_handle._name,
                )
                value = await proxy.request_topic(self._name, [["TCPROS"]])

                _, host, port = value
                assert isinstance(host, str)
                assert isinstance(port, int)

                # Note that this line opens a connection to the host + port combo
                # of the publisher - this connection will NOT be the same as the
                # node handle's TCP/XMLRPC servers. This will be a randomly assigned
                # port.
                reader, writer = await asyncio.open_connection(host, port)
                try:
                    tcpros.send_string(
                        tcpros.serialize_dict(
                            dict(
                                message_definition=self.message_type._full_text,
                                callerid=self._node_handle._name,
                                topic=self._name,
                                md5sum=self.message_type._md5sum,
                                type=self.message_type._type,
                            )
                        ),
                        writer,
                    )
                    header = tcpros.deserialize_dict(
                        await tcpros.receive_string(reader)
                    )
                    self._connections[writer] = header.get("callerid", None)
                    try:
                        while True:
                            data = await tcpros.receive_string(reader)

                            msg = self.message_type().deserialize(data)
                            try:
                                self._callback(msg)
                            except:
                                traceback.print_exc()

                            self._last_message = msg
                            self._last_message_time = self._node_handle.get_time()

                            old, self._message_futs = self._message_futs, []
                            for fut in old:
                                fut.set_result(msg)
                            await asyncio.sleep(0)
                    except (
                        ConnectionRefusedError,
                        BrokenPipeError,
                        ConnectionResetError,
                        asyncio.IncompleteReadError,
                    ):
                        pass
                    finally:
                        del self._connections[writer]
                except (
                    ConnectionRefusedError,
                    BrokenPipeError,
                    ConnectionResetError,
                    asyncio.IncompleteReadError,
                ):
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()
            except (
                ConnectionRefusedError,
                BrokenPipeError,
                ConnectionResetError,
                asyncio.IncompleteReadError,
            ):
                pass
            except Exception:
                traceback.print_exc()

            await util.wall_sleep(
                1
            )  # pause so that we don't repeatedly reconnect immediately on failure

    def _handle_publisher_list(self, publishers: list[str]) -> tuple[int, str, bool]:
        new = {
            k: self._publisher_threads.pop(k)
            if k in self._publisher_threads
            else asyncio.create_task(self._publisher_thread(k))
            for k in publishers
        }
        for k, v in self._publisher_threads.items():
            v.cancel()
        self._publisher_threads = new

        return 1, "success", False
