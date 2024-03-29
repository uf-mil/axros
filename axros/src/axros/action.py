from __future__ import annotations

import asyncio
import random
import traceback
import warnings
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generic

import genpy
from actionlib_msgs.msg import GoalID, GoalStatus, GoalStatusArray
from axros.exceptions import AlreadySetup, NotSetup
from std_msgs.msg import Header

from . import types, util

if TYPE_CHECKING:
    from .nodehandle import NodeHandle


class GoalManager(Generic[types.Goal, types.Feedback, types.Result]):
    """
    Manages the interactions between a specific goal and an action client.

    This class is not meant to be constructed by client objects; rather it is instantiated
    by the :class:`~.ActionClient` to talk to the :class:`~.SimpleActionServer`.
    """

    _action_client: ActionClient[types.Goal, types.Feedback, types.Result]
    _goal: types.Goal
    _goal_id: str
    _feedback_futs: list[asyncio.Future[types.Feedback]]

    def __init__(
        self,
        action_client: ActionClient[types.Goal, types.Feedback, types.Result],
        goal: types.Goal,
    ):
        """
        Args:
            action_client (ActionClient): The axros action client to use to manage
                the goal.
            goal (Goal): The axros goal to manage.
        """
        self._action_client = action_client
        self._goal = goal

        self._goal_id = f"{random.randrange(2**64):016x}"

        assert self._goal_id not in self._action_client._goal_managers
        self._action_client._goal_managers[self._goal_id] = self

        self._feedback_futs = []
        self._result_fut = asyncio.Future()

        self._think_thread = asyncio.create_task(self._think())

    async def _think(self) -> None:
        try:
            now = self._action_client._node_handle.get_time()

            await self._action_client.wait_for_server()

            self._action_client._goal_pub.publish(
                self._action_client._goal_type(
                    header=Header(
                        stamp=now,
                    ),
                    goal_id=GoalID(
                        stamp=now,
                        id=self._goal_id,
                    ),
                    goal=self._goal,
                )
            )
        except:
            traceback.print_exc()

    def __str__(self) -> str:
        return f"<axros.GoalManager at 0x{id(self):0x}, action_client={self._action_client} goal={self._goal}>"

    def _status_callback(self, status):
        del status

    def _result_callback(self, status, result: types.Result):
        del status
        self.forget()

        if not self._result_fut.done():
            self._result_fut.set_result(result)

    def _feedback_callback(self, status, feedback: types.Feedback):
        del status

        old, self._feedback_futs = self._feedback_futs, []
        for fut in old:
            fut.set_result(feedback)

    def get_result(self) -> asyncio.Future[types.ActionResult[types.Result]]:
        """
        Gets the result of the goal from the manager.

        Returns:
            Any: ???
        """
        return self._result_fut

    def get_feedback(self) -> asyncio.Future[types.ActionFeedback[types.Feedback]]:
        """
        Gets the feedback from all feedback :class:`asyncio.Future` objects.
        """
        fut = asyncio.Future()
        self._feedback_futs.append(fut)
        return fut

    def cancel(self) -> None:
        """
        Publishes a message to the action client requesting the goal to be cancelled.
        """
        self._action_client._cancel_pub.publish(
            GoalID(
                stamp=genpy.Time(0, 0),
                id=self._goal_id,
            )
        )

    def forget(self) -> None:
        """
        Leaves the manager running, but requests for the action client to stop
        work on the specific goal.
        """
        if self._goal_id in self._action_client._goal_managers:
            del self._action_client._goal_managers[self._goal_id]


class Goal(Generic[types.Goal]):
    """
    Implementation of a goal object in the axros adaptation of the Simple Action
    Server.

    .. container:: operations

        .. describe:: x == y

            Determines equality between two goals by comparing their ids. If
            either side is not a :class:`actionlib_msgs.msg.GoalStatus`, then
            the result is ``False``.

    Parameters:
        goal (GoalStatus): The original goal message which constructs this class
        status (uint8): An enum representing the status of
        status_text (str): A string representing the status of the goal
    """

    goal: types.ActionGoal[types.Goal] | None  # This needs to be defined
    goal_id: GoalID
    status: int
    status_text: str

    def __init__(
        self,
        goal_msg: types.ActionGoal[types.Goal],
        status: int = GoalStatus.PENDING,
        status_text: str = "",
    ):
        if goal_msg.goal_id.id == "":
            self.goal = None
        self.goal = goal_msg
        self.status = status
        self.status_text = status_text
        self.goal_id = self.goal.goal_id

    def __eq__(self, rhs: Any):
        # assert isinstance(self.goal, GoalStatus), f"Value was {type(self.goal)}: {self.goal}"
        if isinstance(self.goal, GoalStatus) and isinstance(rhs.goal, GoalStatus):
            return self.goal.goal_id.id == rhs.goal.goal_id.id
        return False

    def __str__(self) -> str:
        return f"<axros.Goal at 0x{id(self):0x}, goal={self.goal} status={self.status} status_text={self.status_text}>"

    def status_msg(self) -> GoalStatus:
        """
        Constructs a GoalStatus message from the Goal class.

        Returns:
            GoalStatus: The constructed message.
        """
        msg = GoalStatus()

        # Type checking
        # assert isinstance(self.goal, GoalStatus), f"Value was {type(self.goal)}: {self.goal}"

        # Assemble message
        msg.goal_id = self.goal.goal_id if self.goal else GoalID()
        msg.status = self.status
        msg.text = self.status_text
        return msg


class SimpleActionServer(Generic[types.Goal, types.Feedback, types.Result]):
    """
    A simplified implementation of an action server. At a given time, can only at most
    have a current goal and a next goal. If new goals arrive with one already queued
    to be next, the goal with the greater timestamp will bump to other.

    .. code-block:: python

        >>> # Construct a SimpleActionServer with a node handle, topic namespace, and type
        >>> serv = SimpleActionServer(nh, '/test_action', turtle_actionlib.msg.ShapeAction)
        >>> # The server must be started before any goals can be accepted
        >>> serv.start()
        >>> # To accept a goal
        >>> while not self.is_new_goal_available():  # Loop until there is a new goal
        ...     await nh.sleep(0.1)
        >>> goal = serv.accept_new_goal()
        >>> # To publish feedback
        >>> serv.publish_feedback(ShapeFeedback())
        >>> # To accept a preempt (a new goal attempted to replace the current one)
        >>> if self.is_preempt_requested():
        ...     goal = serv.accept_new_goal()  # Automatically cancels old goal
        >>> # To finish a goal
        >>> serv.set_succeeded(text='Wahoo!', result=ShapeResult(apothem=1))
        >>> # or
        >>> serv.set_aborted(text='Something went wrong!')

    .. container:: operations

        .. describe:: async with x:

            On entering the block, :meth:`~.setup` is called; upon exit, :meth:`~.shutdown`
            is called.
    """

    # TODO:
    # - implement optional callbacks for new goals
    # - ensure headers are correct for each message

    goal: Goal[types.Goal] | None
    next_goal: Goal[types.Goal] | None

    def __init__(
        self,
        node_handle: NodeHandle,
        name: str,
        action_type: type[types.Action[types.Goal, types.Feedback, types.Result]],
        goal_cb: Callable[[], None] | None = None,
        preempt_cb: Callable[[], None] | None = None,
    ):
        self.started = False
        self._node_handle = node_handle
        self._name = name

        self.goal = None
        self.next_goal = None
        self.cancel_requested = False

        self.register_goal_callback(goal_cb)
        self.register_preempt_callback(preempt_cb)

        self.goal_cb = None
        self.preempt_cb = None

        self.status_frequency = 5.0

        self._goal_type = type(action_type().action_goal)
        self._result_type = type(action_type().action_result)
        self._feedback_type = type(action_type().action_feedback)

        self._status_pub = self._node_handle.advertise(
            self._name + "/status", GoalStatusArray
        )
        self._result_pub = self._node_handle.advertise(
            self._name + "/result", self._result_type
        )
        self._feedback_pub = self._node_handle.advertise(
            self._name + "/feedback", self._feedback_type
        )

        self._goal_sub = self._node_handle.subscribe(
            self._name + "/goal", self._goal_type, self._goal_cb
        )
        self._cancel_sub = self._node_handle.subscribe(
            self._name + "/cancel", GoalID, self._cancel_cb
        )
        self._is_running = False

    async def setup(self) -> None:
        """
        Sets up the action server. This must be called before the action server
        can be used.
        """
        if self.is_running():
            raise AlreadySetup(self, self._node_handle)

        await asyncio.gather(
            self._status_pub.setup(),
            self._result_pub.setup(),
            self._feedback_pub.setup(),
            self._goal_sub.setup(),
            self._cancel_sub.setup(),
        )
        self._is_running = True

    async def shutdown(self) -> None:
        """
        Shuts the action server down. This must be called before the program is
        ended.
        """
        await asyncio.gather(
            self._status_pub.shutdown(),
            self._result_pub.shutdown(),
            self._feedback_pub.shutdown(),
            self._goal_sub.shutdown(),
            self._cancel_sub.shutdown(),
        )
        self._is_running = False

    def is_running(self) -> bool:
        """
        Returns:
            bool: Whether the simple action server is running. Set to ``True``
            when :meth:`~.setup` is called; set to ``False`` when :meth:`~.shutdown`
            is called. This is independent of whether :meth:`~.start` or :meth:`~.stop`
            is called.
        """
        return self._is_running

    def __del__(self):
        # Shutdown can also be achieved by just shutting down each pub/sub
        subs_pubs_running = (
            self._status_pub.is_running()
            or self._result_pub.is_running()
            or self._feedback_pub.is_running()
            or self._goal_sub.is_running()
            or self._cancel_sub.is_running()
        )
        if subs_pubs_running and self._is_running:
            warnings.simplefilter("always", ResourceWarning)
            warnings.warn(
                f"The '{self._name}' action server was never shutdown(). This may cause issues with this instance of ROS - please fix the errors and completely restart ROS.",
                ResourceWarning,
            )
            warnings.simplefilter("default", ResourceWarning)

    async def __aenter__(
        self,
    ) -> SimpleActionServer[types.Goal, types.Feedback, types.Result]:
        await self.setup()
        return self

    async def __aexit__(
        self, exc_type: type[Exception], exc_value: Exception, traceback: TracebackType
    ):
        await self.shutdown()

    def __str__(self) -> str:
        return f"<axros.SimpleActionServer at 0x{id(self):0x}, name='{self._name}' running={self.is_running()} started={self.started} goal={self.goal} node_handle={self._node_handle}>"

    def register_goal_callback(self, func: Callable[[], None] | None) -> None:
        self.goal_cb = func

    def _process_goal_callback(self):
        if self.goal_cb:
            self.goal_cb()

    def _process_preempt_callback(self):
        if self.preempt_cb:
            self.preempt_cb()

    def register_preempt_callback(self, func: Callable[[], None] | None) -> None:
        self.preempt_cb = func

    def start(self) -> None:
        """
        Starts the status loop for the server.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self.started = True
        self._status_loop_future = asyncio.create_task(self._status_loop())

    def stop(self) -> None:
        """
        Stops the status loop for the server, and clears all running goals and all
        goals scheduled to be run.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self.goal = None
        self.next_goal = None
        self.started = False

    def accept_new_goal(self) -> types.Goal | None:
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        if not self.started:
            print(
                "SIMPLE ACTION SERVER: attempted to accept_new_goal without being started"
            )
            return None
        if not self.next_goal:
            print(
                "SIMPLE ACTION SERVER: attempted to accept_new_goal when no new goal is available"
            )
        if self.goal:
            self.set_preempted(text="New goal accepted in simple action server")
        self.goal = self.next_goal
        assert self.goal is not None
        self.cancel_requested = False
        self.next_goal = None
        self.goal.status = GoalStatus.ACTIVE if self.goal else GoalStatus.SUCCEEDED
        self._publish_status()
        assert self.goal.goal is not None
        return self.goal.goal.goal

    def is_new_goal_available(self) -> bool:
        """
        Returns:
            bool: Whether the next goal is defined, or not ``None``.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        return self.next_goal is not None

    def is_preempt_requested(self) -> bool:
        """
        Returns:
            bool: Whether the goal has been requested to be cancelled, and there is both
            a goal currently running and a goal scheduled to be run shortly.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        return bool(self.goal) if self.next_goal else self.is_cancel_requested()

    def is_cancel_requested(self) -> bool:
        """
        Returns:
            bool: Whether a goal is currently active and a cancel has been requested.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        return self.goal is not None and self.cancel_requested

    def is_active(self) -> bool:
        """
        Returns:
            bool: Returns whether there is an active goal running.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        return self.goal is not None

    def _set_result(
        self,
        status: int = GoalStatus.SUCCEEDED,
        text: str = "",
        result: types.Result | None = None,
    ) -> None:
        if not self.started:
            print("SimpleActionServer: attempted to set_succeeded before starting")
            return
        if not self.goal:
            print("SimpleActionServer: attempted to set_succeeded without a goal")
            return
        self.goal.status = status
        self.goal.status_text = text
        self._publish_status()
        result_msg = self._result_type()
        if result:
            result_msg.result = result
        result_msg.status = self.goal.status_msg()
        self._result_pub.publish(result_msg)
        self.goal = None

    def set_succeeded(self, result: types.Result | None = None, text: str = "") -> None:
        """
        Sets the status of the current goal to be succeeded.

        Args:
            result (Optional[genpy.Message]): The message to attach in the result.
            text (str): The text to set in the result. Defaults to an empty string.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self._set_result(status=GoalStatus.SUCCEEDED, text=text, result=result)

    def set_aborted(self, result: types.Result | None = None, text: str = "") -> None:
        """
        Sets the status of the current goal to aborted.

        Args:
            result (Optional[genpy.Message]): The message to attach in the result.
            text (str): The text to set in the result. Defaults to an empty string.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self._set_result(status=GoalStatus.ABORTED, text=text, result=result)

    def set_preempted(self, result: types.Result | None = None, text: str = "") -> None:
        """
        Sets the status of the current goal to preempted.

        Args:
            result (Optional[genpy.Message]): The message to attach in the result.
            text (str): The text to set in the result. Defaults to an empty string.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self._set_result(status=GoalStatus.PREEMPTED, text=text, result=result)

    def publish_feedback(self, feedback: types.Feedback | None = None) -> None:
        """
        Publishes a feedback message onto the feedback topic.

        Args:
            feedback (Optional[genpy.Message]): The optional feedback message to add
                to the sent message.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        if not self.started:
            print("SimpleActionServer: attempted to publish_feedback before starting")
            return
        if not self.goal:
            print("SimpleActionServer: attempted to publish_feedback without a goal")
            return
        feedback_msg = self._feedback_type()
        feedback_msg.status = self.goal.status_msg()
        if feedback:
            feedback_msg.feedback = feedback
        self._feedback_pub.publish(feedback_msg)

    def _publish_status(self, goal=None) -> None:
        msg = GoalStatusArray()
        if self.goal:
            msg.status_list.append(self.goal.status_msg())
        if self.next_goal:
            msg.status_list.append(self.next_goal.status_msg())
        if goal:
            msg.status_list.append(goal.status_msg())
        self._status_pub.publish(msg)

    async def _status_loop(self):
        while self.started:
            self._publish_status()
            await self._node_handle.sleep(1.0 / self.status_frequency)

    def _cancel_cb(self, msg: GoalID):
        cancel_current = False
        cancel_next = False
        if msg.stamp != genpy.Time():
            if self.next_goal and self.next_goal.goal_id.stamp < msg.stamp:
                cancel_next = True
            if self.goal and self.goal.goal_id.stamp < msg.stamp:
                cancel_current = True

        if msg.id == "" and msg.stamp == genpy.Time():
            cancel_current = self.goal is not None
            cancel_next = self.next_goal is not None
        elif msg.id != "":
            if self.goal and msg.id == self.goal.goal_id.id:
                cancel_current = True
            if self.next_goal and msg.id == self.next_goal.goal_id.id:
                cancel_next = True
        if cancel_next:
            assert self.next_goal is not None
            self.next_goal.status = GoalStatus.RECALLED
            self.next_goal.status_text = "Goal canceled"
            result_msg = self._result_type()
            result_msg.status = self.next_goal.status_msg()
            self._result_pub.publish(result_msg)
            self._publish_status()
            self.next_goal = None
        if cancel_current and not self.cancel_requested:
            self.cancel_requested = True
            self._process_preempt_callback()

    def _goal_cb(self, msg: types.ActionGoal[types.Goal]) -> None:
        if not self.started:
            return None
        new_goal = Goal(msg)
        if new_goal.goal is None:  # If goal field is empty, invalid goal
            return None
        # Throw out duplicate goals
        if (self.goal and new_goal == self.goal) or (
            self.next_goal and new_goal == self.next_goal
        ):
            return None
        now = self._node_handle.get_time()
        if (
            new_goal.goal.goal_id.stamp == genpy.Time()
        ):  # If time is not set, replace with current time
            new_goal.goal.goal_id.stamp = now
        if (
            self.next_goal is not None and self.next_goal.goal is not None
        ):  # If another goal is queued, handle conflict
            # If next goal is later, rejct new goal
            if new_goal.goal.goal_id.stamp < self.next_goal.goal.goal_id.stamp:
                new_goal.status = GoalStatus.REJECTED
                new_goal.status_text = "canceled because another goal was received by the simple action server"
                result_msg = self._result_type()
                result_msg.header.stamp = now
                result_msg.status = new_goal.status_msg()
                self._result_pub.publish(result_msg)
                self._publish_status(goal=new_goal)
                return None
            else:  # New goal is later so reject current next_goal
                self.next_goal.status = GoalStatus.REJECTED
                self.next_goal.status_text = "Goal bumped by newer goal"
                result_msg = self._result_type()
                result_msg.header.stamp = now
                result_msg.status = self.next_goal.status_msg()
                self._result_pub.publish(result_msg)
                self._publish_status()
        self.next_goal = new_goal
        self._publish_status()
        if self.goal:
            self._process_preempt_callback()
        else:
            self._process_goal_callback()


class ActionClient(Generic[types.Goal, types.Feedback, types.Result]):
    """
    Representation of an action client in axros. This works in conjunction with
    the :class:`~.SimpleActionServer` by sending the servers goals to execute.
    In response, the client receives feedback updates and the final result of the goal,
    which it can handle by itself.
    """

    def __init__(
        self, node_handle: NodeHandle, name: str, action_type: type[types.Action[types.Goal, types.Feedback, types.Result]]
    ):
        """
        Args:
            node_handle (axros.NodeHandle): Node handle used to power the action client.
            name (str): The name of the action client.
            action_type (type[genpy.Message]): The action message type used by the
                action client and server.
        """
        self._node_handle = node_handle
        self._name = name
        self._type = action_type
        self._goal_type = type(action_type().action_goal)
        self._result_type = type(action_type().action_result)
        self._feedback_type = type(action_type().action_feedback)

        self._goal_managers: dict[str, GoalManager] = {}

        self._goal_pub = self._node_handle.advertise(
            self._name + "/goal", self._goal_type
        )
        self._cancel_pub = self._node_handle.advertise(self._name + "/cancel", GoalID)
        self._status_sub = self._node_handle.subscribe(
            self._name + "/status", GoalStatusArray, self._status_callback
        )
        self._result_sub = self._node_handle.subscribe(
            self._name + "/result", self._result_type, self._result_callback
        )
        self._feedback_sub = self._node_handle.subscribe(
            self._name + "/feedback", self._feedback_type, self._feedback_callback
        )
        self._is_running = False

    async def setup(self):
        """
        Sets up the action client. This must be called before the action client
        can be used.
        """
        if self.is_running():
            raise AlreadySetup(self, self._node_handle)

        await asyncio.gather(
            self._goal_pub.setup(),
            self._cancel_pub.setup(),
            self._status_sub.setup(),
            self._result_sub.setup(),
            self._feedback_sub.setup(),
        )
        self._is_running = True

    async def shutdown(self):
        """
        Shuts down the action client. This should always be called when the action
        client is no longer needed.
        """
        await asyncio.gather(
            self._goal_pub.shutdown(),
            self._cancel_pub.shutdown(),
            self._status_sub.shutdown(),
            self._result_sub.shutdown(),
            self._feedback_sub.shutdown(),
        )
        self._is_running = False

    def is_running(self) -> bool:
        return self._is_running

    def __str__(self) -> str:
        return f"<axros.ActionClient at 0x{id(self):0x}, name='{self._name}' running={self.is_running()} node_handle={self._node_handle}>"

    def _status_callback(self, msg: GoalStatusArray) -> None:
        for status in msg.status_list:
            if status.goal_id.id in self._goal_managers:
                manager = self._goal_managers[status.goal_id.id]
                manager._status_callback(status.status)

    def _result_callback(self, msg: types.ActionResult[types.Result]) -> None:
        if msg.status.goal_id.id in self._goal_managers:
            manager = self._goal_managers[msg.status.goal_id.id]
            manager._result_callback(msg.status.status, msg.result)

    def _feedback_callback(self, msg: types.ActionFeedback[types.Feedback]):
        if msg.status.goal_id.id in self._goal_managers:
            manager = self._goal_managers[msg.status.goal_id.id]
            manager._feedback_callback(msg.status.status, msg.feedback)

    def send_goal(self, goal: types.Goal) -> GoalManager[types.Goal, types.Feedback, types.Result]:
        """
        Sends a goal to a goal manager. The goal manager is responsible for the
        communication between the action client and server; it assists in this process
        by maintaining individual :class:`asyncio.Future` objects.

        Raises:
            NotSetup: The action client has not been :meth:`~.setup`, or was previously
                :meth:`~.shutdown`.

        Returns:
            GoalManager: The manager of the goal.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        return GoalManager(self, goal)

    def cancel_all_goals(self) -> None:
        """
        Sends a message to the mission cancellation topic requesting all goals to
        be cancelled.

        Raises:
            NotSetup: The action client has not been :meth:`~.setup`, or was previously
                :meth:`~.shutdown`.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self._cancel_pub.publish(
            GoalID(
                stamp=genpy.Time(0, 0),
                id="",
            )
        )

    def cancel_goals_at_and_before_time(self, time: genpy.rostime.Time) -> None:
        """
        Cancel all goals scheduled at and before a specific time.

        Args:
            time: The time to reference when selecting which goals to cancel.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        self._cancel_pub.publish(
            GoalID(
                stamp=time,
                id="",
            )
        )

    async def wait_for_server(self):
        """
        Waits for a server connection. When at least one connection is received,
        the function terminates.
        """
        if not self.is_running():
            raise NotSetup(self, self._node_handle)

        while not (
            set(self._goal_pub.get_connections())
            & set(self._cancel_pub.get_connections())
            & set(self._status_sub.get_connections())
            & set(self._result_sub.get_connections())
            & set(self._feedback_sub.get_connections())
        ):
            await util.wall_sleep(0.1)  # XXX bad bad bad
