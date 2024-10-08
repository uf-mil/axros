from __future__ import annotations

import asyncio
import traceback
import warnings
from io import BytesIO
from types import TracebackType
from typing import TYPE_CHECKING, Generic, TypeVar

from axros import exceptions, tcpros, types

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

M = TypeVar("M", bound=types.Message)


class Publisher(Generic[M]):
    """
    A Publisher in the axros suite. Allows for the publishing of messages onto a
    certain ROS topic. This class should usually be made through :meth:`axros.NodeHandle.advertise`.

    Before use, the publisher must be :meth:`~.setup` and after use, it must be
    :meth:`~.shutdown`.

    .. container:: operations

        .. describe:: async with x:

            On entering the block, the publisher is :meth:`~.setup`; upon leaving the block,
            the publisher is :meth:`~.shutdown`.

    Attributes:
        message_type (type[genpy.Message]): The type of message being published
            on the topic.
    """

    _connections: dict[asyncio.StreamWriter, str]
    _name: str
    _node_handle: NodeHandle
    _is_running: bool

    def __init__(
        self,
        node_handle: NodeHandle,
        name: str,
        message_type: type[M],
        latching: bool = False,
    ):
        """
        Args:
            node_handle (NodeHandle): The node handle to associate with this publisher.
                Used to communicate with the ROS master server.
            name (str): The name of the publisher topic.
            message_type (Type[genpy.Message]): The message type that will be published
                on the topic.
            latching (bool): Enables latching on the publisher. This ensures that all
                new connections are immediately sent the most recently sent message
                when they connect to the publisher.
        """
        self._node_handle = node_handle
        self._name = self._node_handle.resolve_name(name)
        self.message_type = message_type
        self._latching = latching

        self._last_message_data = None
        self._connections = {}

        self._node_handle.shutdown_callbacks.add(self.shutdown)
        self._is_running = False
        self._shutdown_event = asyncio.Event()

    def __str__(self) -> str:
        return (
            f"<axros.Publisher at 0x{id(self):0x}, "
            f"name={self._name} "
            f"running={self.is_running()} "
            f"message_type={self.message_type} "
            f"latching={self._latching} "
            f"node_handle={self._node_handle}>"
        )

    __repr__ = __str__

    async def setup(self) -> None:
        """
        Sets up the publisher by registering the name of the publisher with ROS
        and registering callbacks associated with the publisher with the parent
        node handle.

        This should always be called before the publisher is ever used.
        """
        if self.is_running():
            raise exceptions.AlreadySetup(self, self._node_handle)

        if ("topic", self._name) in self._node_handle.tcpros_handlers:
            self._node_handle.tcpros_handlers[("topic", self._name)].append(
                self._handle_tcpros_conn
            )
        else:
            self._node_handle.tcpros_handlers[("topic", self._name)] = [
                self._handle_tcpros_conn
            ]

        if ("requestTopic", self._name) in self._node_handle.xmlrpc_handlers:
            self._node_handle.xmlrpc_handlers[("requestTopic", self._name)].append(
                self._handle_requestTopic
            )
        else:
            self._node_handle.xmlrpc_handlers[("requestTopic", self._name)] = [
                self._handle_requestTopic
            ]

        # Register the publisher with the master ROS node
        await self._node_handle.master_proxy.register_publisher(
            self._name,
            self.message_type._type,
            self._node_handle.xmlrpc_server_uri,
        )
        self._is_running = True

    def __del__(self):
        if self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The '{self._name}' publisher (in '{self._node_handle.get_name()}') was never shutdown(). This may cause issues with this instance of ROS - please fix the errors and completely restart ROS.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)

    async def __aenter__(self) -> Publisher:
        await self.setup()
        return self

    async def __aexit__(
        self, exc_type: type[Exception], exc_value: Exception, traceback: TracebackType
    ):
        await self.shutdown()

    async def _close_connections(self):
        self._shutdown_event.set()
        while self._connections:
            await asyncio.sleep(0.1)

    async def shutdown(self):
        """
        Shuts the publisher down. All operations scheduled by the publisher are cancelled.

        Raises:
            ResourceWarning: The publisher is not running. It may need to be :meth:`~.setup`.
        """
        if not self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The {self._name} subscriber (in '{self._node_handle.get_name()}') is not currently running. It may have been shutdown previously or never started.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)
            return

        try:
            await self._node_handle.master_proxy.unregister_publisher(
                self._name, self._node_handle.xmlrpc_server_uri
            )
        except Exception:
            traceback.print_exc()

        await self._close_connections()

        handlers = self._node_handle.tcpros_handlers[("topic", self._name)]
        handlers.remove(self._handle_tcpros_conn)
        if not handlers:
            del self._node_handle.tcpros_handlers[("topic", self._name)]

        handlers = self._node_handle.xmlrpc_handlers[("requestTopic", self._name)]
        handlers.remove(self._handle_requestTopic)
        if not handlers:
            del self._node_handle.xmlrpc_handlers[("requestTopic", self._name)]

        self._node_handle.shutdown_callbacks.discard(self.shutdown)
        self._is_running = False

    def _handle_requestTopic(self, protocols) -> tuple[int, str, list[str | int]]:
        del protocols  # This method is protocol-agnostic
        return (
            1,
            "ready on " + self._node_handle._tcpros_server_uri,
            [
                "TCPROS",
                self._node_handle._tcpros_server_addr[0],
                self._node_handle._tcpros_server_addr[1],
            ],
        )

    def is_running(self) -> bool:
        """
        Returns:
            bool: Whether the publisher is running. ``True`` when the publisher
            is :meth:`~.setup` and ``False`` otherwise (including when the node
            handle or publisher is shutdown).
        """
        return self._is_running

    async def _handle_tcpros_conn(
        self,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            tcpros.send_string(
                tcpros.serialize_dict(
                    dict(
                        callerid=self._node_handle._name,
                        type=self.message_type._type,
                        md5sum=self.message_type._md5sum,
                        latching="1" if self._latching else "0",
                    )
                ),
                writer,
            )

            if self._latching and self._last_message_data is not None:
                tcpros.send_string(self._last_message_data, writer)

            self._connections[writer] = headers["callerid"]
            await self._shutdown_event.wait()
        except (asyncio.IncompleteReadError, BrokenPipeError, ConnectionResetError):
            # Exceptions related to client exiting - ignore
            pass
        finally:
            del self._connections[writer]
            writer.close()
            await writer.wait_closed()

    def publish(self, msg: M) -> None:
        """
        Publishes a message onto the topic. The message is serialized and sent to
        all connections connected to the publisher.

        Args:
            msg (genpy.Message): The ROS message to send to all connected clients.

        Raises:
            TypeError: The message type was invalid.
            NotSetup: The publisher was not :meth:`~.setup` or was previously :meth:`~.shutdown`.
        """
        if not isinstance(msg, self.message_type):
            raise TypeError(
                f"Cannot publish message of type {msg.__class__} on {self._name} publisher, which only accepts {self.message_type}."
            )

        if not self.is_running():
            raise exceptions.NotSetup(self, self._node_handle)

        buff = BytesIO()
        msg.serialize(buff)
        data = buff.getvalue()

        for conn in self._connections:
            tcpros.send_string(data, conn)

        if self._latching:
            self._last_message_data = data

    def get_connections(self):
        """
        Gets connections to the publisher.

        Returns:
            list[str]: The list of nodes connected to the publisher.
        """
        return list(self._connections.values())
