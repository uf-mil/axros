"""
Utility functions which make transforms and asynchronous programming easier.

All non-private methods can be used throughout client and application code.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

import genpy

T = TypeVar("T")


async def wall_sleep(duration: genpy.Duration | float) -> None:
    """
    Sleeps for a specified duration using :func:`asyncio.sleep`.

    Args:
        duration (genpy.Duration | :class:`float`): The amount of time to sleep for.
    """
    if isinstance(duration, genpy.Duration):
        duration = duration.to_sec()
    elif not isinstance(duration, (float, int)):
        raise TypeError("expected float or genpy.Duration")
    await asyncio.sleep(duration)


async def wrap_timeout(
    fut: Awaitable[T], duration: float | genpy.Duration, *, cancel=True
) -> T:
    """
    Wraps a given future in a timeout-based future. This can be used to ensure
    that select :class:`asyncio.Future` objects complete on time. Futures who
    do not complete on time can be optionally cancelled.

    For an equivalent method that does not raise an exception, see
    :meth:`txros.wrap_time_notice`.

    Args:
        fut (:class:`asyncio.Future` | :class:`coroutine`): The future object to timeout.
        duration (:class:`float` | genpy.Duration): The duration to timeout.
        cancel (:class:`bool`): Keyword-only argument designating whether to
            cancel the future when the task times out.

    Raises:
        asyncio.TimeoutError: The task did not complete on time.

    Returns:
        Any: The data returned from the awaitable.
    """
    if isinstance(duration, genpy.Duration):
        timeout = duration.to_sec()
    else:
        timeout = float(duration)

    if cancel:
        return await asyncio.wait_for(fut, timeout)
    return await asyncio.wait_for(asyncio.shield(fut), timeout)


async def _print_message_helper(duration: float, description: str):
    await asyncio.sleep(duration)
    print(f"{description} is taking a while...")


async def wrap_time_notice(
    fut: Awaitable[T], duration: float | genpy.Duration, description: str
) -> T:
    """
    Prints a message if a future is taking longer than the noted duration.

    Args:
        fut (:class:`asyncio.Future`): The future object.
        duration (:class:`float` | genpy.Duration): The duration to wait before printing
            a message.
        description (:class:`str`): The description to print.

    Returns:
        Any: The result from the awaitable.
    """
    if isinstance(duration, genpy.Duration):
        timeout = duration.to_sec()
    else:
        timeout = float(duration)

    task = asyncio.create_task(_print_message_helper(timeout, description))
    result = await fut
    task.cancel()

    print(f"{description} succeeded!")
    return result
