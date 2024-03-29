"""
:mod:`axros` provides independent extensions to ROS that can be used across other
packages. These extensions primarily focus on asynchronous extensions for most
ROS classes, including nodes, subscribers, and publishers. Furthermore, proxies to
ROS over TCP are provided for communication inside the package.

Generally, this package is used for the :class:`~axros.NodeHandle` class and its
neighbors in client code. Classes in this module are generally not inherited from
for extensions.
"""
from .action import ActionClient, Goal, GoalManager, SimpleActionServer
from .axros_tf import TooPastError, Transform, TransformBroadcaster, TransformListener
from .exceptions import AlreadySetup, AxrosException, NotSetup
from .nodehandle import NodeHandle
from .publisher import Publisher
from .rosxmlrpc import (
    AsyncioTransport,
    AsyncServerProxy,
    ROSMasterError,
    ROSMasterFailure,
    ROSMasterProxy,
    XMLRPCException,
    XMLRPCLegalType,
)
from .service import Service
from .serviceclient import ServiceClient, ServiceError
from .subscriber import Subscriber
from .util import wall_sleep, wrap_time_notice, wrap_timeout
