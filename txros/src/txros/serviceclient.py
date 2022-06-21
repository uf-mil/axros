from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING, Protocol, Type

import genpy
from twisted.internet import defer, endpoints, reactor

from . import rosxmlrpc, tcpros, util

if TYPE_CHECKING:
    from .nodehandle import NodeHandle


class ServiceType(Protocol):
    _type: str
    _md5sum: str
    _request_class: Type[genpy.Message]
    _response_class: Type[genpy.Message]


class ServiceError(Exception):
    """
    Represents an error with a service client in txROS.

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
        return "ServiceError(%r)" % (self._message,)

    __repr__ = __str__


class ServiceClient:
    """
    A client connected to a service in txROS.

    .. container:: operations

        .. describe:: x(request_class)

            Makes a request to the service using an instance of the ``request_class``
            request type. This operation returns a Deferred containing the result
            sent by the topic through the master server. Any errors will raise an
            instance of :class:`txros.ServiceError`.
    """

    def __init__(self, node_handle: NodeHandle, name: str, service_type: ServiceType):
        """
        Args:
            node_handle (NodeHandle): The node handle serving as the client to the service.
            name (str): The name of the service to connect to.
        """
        self._node_handle = node_handle
        self._name = self._node_handle.resolve_name(name)
        self._type = service_type

    @util.cancellableInlineCallbacks
    def __call__(self, req: genpy.Message):
        serviceUrl = yield self._node_handle._master_proxy.lookupService(self._name)

        protocol, rest = serviceUrl.split("://", 1)
        host, port_str = rest.rsplit(":", 1)
        port = int(port_str)

        assert protocol == "rosrpc"

        conn = yield endpoints.TCP4ClientEndpoint(reactor, host, port).connect(
            util.AutoServerFactory(lambda addr: tcpros.Protocol())
        )
        try:
            conn.sendString(
                tcpros.serialize_dict(
                    dict(
                        callerid=self._node_handle._name,
                        service=self._name,
                        md5sum=self._type._md5sum,
                        type=self._type._type,
                    )
                )
            )

            tcpros.deserialize_dict((yield conn.receiveString()))

            # request could be sent before header is received to reduce latency...
            x = StringIO()
            self._type._request_class.serialize(req, x)
            data = x.getvalue()
            conn.sendString(data)

            result = ord((yield conn.receiveByte()))
            data = yield conn.receiveString()
            if result:  # success
                defer.returnValue(self._type._response_class().deserialize(data))
            else:
                raise ServiceError(data)
        finally:
            conn.transport.loseConnection()

    @util.cancellableInlineCallbacks
    def wait_for_service(self):
        """
        Waits for the service to appear. Checks to see if the service has appeared
        10 times per second.
        """
        while True:
            try:
                yield self._node_handle._master_proxy.lookupService(self._name)
            except rosxmlrpc.Error:
                yield util.wall_sleep(0.1)  # XXX bad
                continue
            else:
                return
