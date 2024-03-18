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


class AxrosException(Exception):
    """
    Exception related to axros. Inherits from :class:`Exception`.

    Attributes:
        node_handle (axros.NodeHandle): The node handle which caused the exception.
    """

    def __init__(self, message: str, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(
            f"Exception in axros related to {node_handle._name}: {message}"
        )


class NotSetup(AxrosException):
    """
    Indicates that a resource (such as a :class:`axros.NodeHandle`) was not setup,
    or was previously shutdown and not setup again. To solve this issue, you will
    likely need to call the class' ``setup()`` method.

    Inherits from :class:`~axros.AxrosException`.
    """

    def __init__(self, resource: Resource, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(
            f"Resource has not been setup(), or was previously shutdown(): {resource}",
            node_handle,
        )


class AlreadySetup(AxrosException):
    """
    Indicates that a resource (such as a :class:`axros.NodeHandle`) was already setup,
    but ``setup()`` was called again.

    Inherits from :class:`~axros.AxrosException`.
    """

    def __init__(self, resource: Resource, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(
            f"Resource was already setup, but setup() was called again: {resource}",
            node_handle,
        )
