from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .action import ActionClient, SimpleActionServer
    from .nodehandle import NodeHandle
    from .publisher import Publisher
    from .service import Service
    from .subscriber import Subscriber

    Resource = Union[
        NodeHandle, Subscriber, Publisher, Service, ActionClient, SimpleActionServer
    ]


class TxrosException(Exception):
    """
    Exception related to txROS. Inherits from :class:`Exception`.

    Attributes:
        node_handle (txros.NodeHandle): The node handle which caused the exception.
    """

    def __init__(self, message: str, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(
            f"Exception in txros related to {node_handle._name}: {message}"
        )


class NotSetup(TxrosException):
    """
    Indicates that a resource (such as a :class:`txros.NodeHandle`) was not setup,
    or was previously shutdown and not setup again. To solve this issue, you will
    likely need to call the class' ``setup()`` method.

    Inherits from :class:`~txros.TxrosException`.
    """

    def __init__(self, resource: Resource, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(
            f"Resource has not been setup(), or was previously shutdown(): {resource}",
            node_handle,
        )
