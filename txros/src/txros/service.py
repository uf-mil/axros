from __future__ import annotations

import asyncio
import traceback
import warnings
from io import BytesIO
from types import TracebackType
from typing import TYPE_CHECKING, Awaitable, Callable, Generic, TypeVar

from . import exceptions, tcpros, types

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

Request = TypeVar("Request", bound=types.Message)
Reply = TypeVar("Reply", bound=types.Message)


class Service(Generic[Request, Reply]):
    """
    A service in the txROS suite. Handles incoming requests through a user-supplied
    asynchronous callback function, which is expected to return a response message.

    This class completes the server aspect of the server-client relationship in
    ROS. The client class is the :class:`txros.ServiceClient` class - this class
    can be used to call services.

    .. container:: operations

        .. describe:: async with x:

            On entering the block, the publisher is :meth:`~.setup`; upon leaving the block,
            the publisher is :meth:`~.shutdown`.
    """

    def __init__(
        self,
        node_handle: NodeHandle,
        name: str,
        service_type: types.ServiceMessage[Request, Reply],
        callback: Callable[[Request], Awaitable[Reply]],
    ):
        """
        Args:
            node_handle (NodeHandle): The node handle to use in conjunction with
                the service.
            name (str): The name to use for the service.
            service_type (ServiceType): A ROS service class to use with the service.
                The callback method used by the class will receive the request
                class associated with the service, and is expected to return the
                response class associated with this class.
            callback (Callable[[genpy.Message], Awaitable[genpy.Message]]): An asynchronous callback
                to process all incoming service requests. The returned message type
                should be the reply type associated with the service.
        """
        self._node_handle = node_handle
        self._name = self._node_handle.resolve_name(name)
        self._type = service_type
        self._callback = callback

        self._node_handle.shutdown_callbacks.add(self.shutdown)
        self._is_running = False

    def __str__(self) -> str:
        return (
            f"<txros.Service at 0x{id(self):0x}, "
            f"name={self._name} "
            f"service_type={self._type} "
            f"running={self._is_running} "
            f"node_handle={self._node_handle}>"
        )

    __repr__ = __str__

    async def setup(self) -> None:
        """
        Sets up the service to be able to receive incoming connections.

        This must be called before the service can be used.
        """
        if self.is_running():
            raise exceptions.AlreadySetup(self, self._node_handle)

        assert ("service", self._name) not in self._node_handle.tcpros_handlers
        self._node_handle.tcpros_handlers["service", self._name] = [
            self._handle_tcpros_conn
        ]
        await self._node_handle.master_proxy.register_service(
            self._name,
            self._node_handle._tcpros_server_uri,
            self._node_handle.xmlrpc_server_uri,
        )
        self._is_running = True

    def is_running(self) -> bool:
        """
        Returns:
            bool: Whether the service is running; ie, able to accept requests.
        """
        return self._is_running

    async def __aenter__(self) -> Service:
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
                f"The '{self._name}' service was never shutdown(). This may cause issues with this instance of ROS - please fix the errors and completely restart ROS.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)

    async def shutdown(self) -> None:
        """
        Shuts the service down. Cancels all operations currently scheduled to be
        completed by the service.
        """
        try:
            await self._node_handle.master_proxy.unregister_service(
                self._name, self._node_handle._tcpros_server_uri
            )
        except Exception:
            traceback.print_exc()
        del self._node_handle.tcpros_handlers["service", self._name]

        self._node_handle.shutdown_callbacks.discard(self.shutdown)
        self._is_running = False

    async def _handle_tcpros_conn(
        self, _, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            # check headers

            tcpros.send_string(
                tcpros.serialize_dict(
                    dict(
                        callerid=self._node_handle._name,
                        type=self._type._type,
                        md5sum=self._type._md5sum,
                        request_type=self._type._request_class._type,
                        response_type=self._type._response_class._type,
                    )
                ),
                writer,
            )

            while True:
                string = await tcpros.receive_string(reader)
                req = self._type._request_class().deserialize(string)
                try:
                    resp = await self._callback(req)
                except Exception as e:
                    traceback.print_exc()
                    tcpros.send_byte(chr(0).encode(), writer)
                    tcpros.send_string(str(e).encode(), writer)
                else:
                    tcpros.send_byte(chr(1).encode(), writer)
                    x = BytesIO()
                    self._type._response_class.serialize(resp, x)
                    tcpros.send_string(x.getvalue(), writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            # Usually means that the client has just disconnected
            pass
        finally:
            writer.close()
            await writer.wait_closed()
