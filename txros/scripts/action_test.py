#!/usr/bin/python3

import asyncio
import random

import uvloop
from actionlib.msg import TestAction, TestGoal

import txros
from txros import action


async def main():
    nh = txros.NodeHandle.from_argv("action_test_node", anonymous=True)
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
