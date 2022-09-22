from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .nodehandle import NodeHandle

class TxrosException(Exception):
    """
    Exception related to txROS. Inherits from :class:`Exception`.

    Attributes:
        node_handle (txros.NodeHandle): The node handle which caused the exception.
    """
    def __init__(self, message: str, node_handle: NodeHandle):
        self.node_handle = node_handle
        super().__init__(f"Exception in txros related to {node_handle._name}: {message}")
