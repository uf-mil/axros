from __future__ import annotations

import asyncio
from io import BytesIO
from typing import TYPE_CHECKING, Callable, Generic, Protocol, Type, TypeVar

import genpy

from . import rosxmlrpc, tcpros, types, util

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

S = TypeVar("S", bound=types.ServiceMessage)


class ServiceType(Protocol):
    _type: str
    _md5sum: str
    _request_class: genpy.Message
    _response_class: genpy.Message


class ServiceError(Exception):
    """
    Represents an error with a service client in axros.

    Inherits from :class:`Exception`.

    .. container:: operations

        .. describe:: str(x)

            Pretty-prints ``ServiceError`` name with the given error message.

        .. describe:: repr(x)

            Pretty-prints ``ServiceError`` name with the given error message.
            Equivalent to ``str(x)``.
    """

    def __init__(self, message: str):
        self._message = message

    def __str__(self):
        return f"ServiceError({self._message!r})"

    __repr__ = __str__


class ServiceClient(Generic[S]):
    """
    A client connected to a service in axros. This is the client class of the
    client-server relationship in ROS; the server is the :class:`axros.Service`
    class.

    .. container:: operations

        .. describe:: await x(request_class)

            Makes a request to the service using an instance of the ``request_class``
            request type. This operation returns the result sent by the topic
            through the master server. Any errors will raise an
            instance of :class:`axros.ServiceError`.
    """

    def __init__(self, node_handle: NodeHandle, name: str, service_type: S):
        """
        Args:
            node_handle (NodeHandle): The node handle serving as the client to the service.
            name (str): The name of the service to connect to.
        """
        self._node_handle = node_handle
        self._name = self._node_handle.resolve_name(name)
        self._type = service_type

    def __str__(self) -> str:
        return (
            f"<axros.ServiceClient at 0x{id(self):0x}, "
            f"name={self._name} "
            f"service_type={self._type} "
            f"node_handle={self._node_handle}>"
        )

    __repr__ = __str__

    async def __call__(self, req: genpy.Message):
        if req.__class__ != self._type._request_class:
            raise TypeError(
                f"Expected {self._type._request_class} message type when calling {self._name} service, but got {req.__class__} instead."
            )

        service_url = await self._node_handle.master_proxy.lookup_service(self._name)

        protocol, rest = service_url.split("://", 1)
        host, port_str = rest.rsplit(":", 1)
        port = int(port_str)

        assert protocol == "rosrpc"

        loop = asyncio.get_event_loop()
        reader, writer = await asyncio.open_connection(host, port)
        try:
            tcpros.send_string(
                tcpros.serialize_dict(
                    dict(
                        callerid=self._node_handle._name,
                        service=self._name,
                        md5sum=self._type._md5sum,
                        type=self._type._type,
                    )
                ),
                writer,
            )

            tcpros.deserialize_dict(await tcpros.receive_string(reader))

            # request could be sent before header is received to reduce latency...
            x = BytesIO()
            self._type._request_class.serialize(req, x)
            data = x.getvalue()
            tcpros.send_string(data, writer)

            result = await tcpros.receive_byte(reader)
            data = await tcpros.receive_string(reader)
            if result:  # success
                return self._type._response_class().deserialize(data)
            else:
                raise ServiceError(data.decode())
        finally:
            writer.close()
            await writer.wait_closed()

    async def wait_for_service(self):
        """
        Waits for the service to appear. Checks to see if the service has appeared
        10 times per second.
        """
        while True:
            try:
                await self._node_handle.master_proxy.lookup_service(self._name)
            except (rosxmlrpc.XMLRPCException, rosxmlrpc.ROSMasterError):
                await util.wall_sleep(0.1)  # XXX bad
                continue
            else:
                return
