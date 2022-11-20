#!/usr/bin/python3

import asyncio
import random

import axros
import uvloop
from actionlib.msg import TestAction, TestGoal
from axros import action


async def main():
    nh = axros.NodeHandle.from_argv("action_test_node", anonymous=True)
    await nh.setup()

    ac = action.ActionClient(nh, "test_action", TestAction)
    await ac.setup()

    x = random.randrange(1000)
    goal_manager = ac.send_goal(
        TestGoal(
            goal=x,
        )
    )
    print("sent goal")

    while True:
        result = await goal_manager.get_result()
        print(f"Result is: {result}")
        await asyncio.sleep(0.1)
    await nh.shutdown()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
