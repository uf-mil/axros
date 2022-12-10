"""
Various classes dedicated specifically to improving type annotations throughout
the repository.
"""
from __future__ import annotations

import asyncio
from abc import ABCMeta, abstractmethod
from io import BytesIO
from typing import (
    Any,
    Callable,
    ClassVar,
    Coroutine,
    Dict,
    Optional,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from actionlib_msgs.msg import GoalID, GoalStatus
from std_msgs.msg import Header

TCPROSHeader = Dict[str, str]
TCPROSProtocol = Callable[
    [TCPROSHeader, asyncio.StreamReader, asyncio.StreamWriter],
    Coroutine[Any, Any, None],
]


@runtime_checkable
class Message(Protocol):
    _md5sum: str
    _type: str
    _has_header: bool
    _full_text: str
    _slot_types: list[str]

    @abstractmethod
    def _get_types(self) -> list[str]:
        ...

    @abstractmethod
    def serialize(self, buff: BytesIO) -> None:
        ...

    @abstractmethod
    def deserialize(self, str: bytes) -> Message:
        ...


@runtime_checkable
class MessageWithHeader(Message, Protocol, metaclass=ABCMeta):
    header: Header


Request = TypeVar("Request", bound=Message)
Response = TypeVar("Response", bound=Message)


class ServiceMessage(Protocol[Request, Response]):
    _type: str
    _md5sum: str
    _request_class: type[Request]
    _response_class: type[Response]


Goal = TypeVar("Goal", bound=Message)
Feedback = TypeVar("Feedback", bound=Message)
Result = TypeVar("Result", bound=Message)


class HasGoal(Protocol[Goal]):
    goal: Goal


@runtime_checkable
class ActionGoal(HasGoal[Goal], MessageWithHeader, Protocol, metaclass=ABCMeta):
    goal_id: GoalID
    goal: Goal

    def __init__(
        self,
        header: Optional[Header] = None,
        goal_id: Optional[GoalID] = None,
        goal: Optional[Goal] = None,
    ) -> None:
        ...

class HasResult(Protocol[Result]):
    result: Result


@runtime_checkable
class ActionResult(HasResult[Result], MessageWithHeader, Protocol, metaclass=ABCMeta):
    status: GoalStatus


class HasFeedback(Protocol[Feedback]):
    feedback: Feedback


@runtime_checkable
class ActionFeedback(
    HasFeedback[Feedback], MessageWithHeader, Protocol, metaclass=ABCMeta
):
    status: GoalStatus


@runtime_checkable
class Action(Protocol[Goal, Feedback, Result]):
    @property
    def action_goal(self) -> ActionGoal[Goal]:
        ...

    @property
    def action_feedback(self) -> ActionFeedback[Feedback]:
        ...

    @property
    def action_result(self) -> ActionResult[Result]:
        ...
