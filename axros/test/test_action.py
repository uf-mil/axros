#! /usr/bin/env python3
import asyncio
import unittest

import axros
import rostest
from actionlib.msg import TestAction, TestFeedback, TestGoal, TestResult
from std_srvs.srv import SetBool, SetBoolRequest, SetBoolResponse


class ActionTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests the subscribing and publishing functionality of axros.
    """

    nh: axros.NodeHandle

    async def asyncSetUp(self):
        self.client_nh = axros.NodeHandle.from_argv(
            "test_action_client", always_default_name=True
        )
        self.server_nh = axros.NodeHandle.from_argv(
            "test_action_server", always_default_name=True
        )
        await asyncio.gather(self.client_nh.setup(), self.server_nh.setup())

    async def serve_simple_requests(self):
        serv = axros.SimpleActionServer(self.server_nh, "/test_action", TestAction)
        print(f"Setting up simple action server...")
        await serv.setup()
        serv.start()
        print(f"Ready to acept new goals!")
        while not serv.is_new_goal_available():
            await asyncio.sleep(0.1)
        serv.accept_new_goal()
        serv.publish_feedback(TestFeedback(999))
        serv.set_succeeded(text="Let's go!", result=TestResult(777))

    async def test_simple_action(self):
        simple_server = asyncio.create_task(self.serve_simple_requests())
        client = axros.ActionClient(self.client_nh, "/test_action", TestAction)
        await client.setup()
        print(f"Waiting for server...")
        await client.wait_for_server()
        print(f"Sending goal...")
        manager = client.send_goal(TestGoal(1))
        result = await manager.get_result()
        self.assertEqual(result.result, 777)

    async def asyncTearDown(self):
        await self.client_nh.shutdown()
        await self.server_nh.shutdown()


if __name__ == "__main__":
    rostest.rosrun("axros", "test_action", ActionTest)
    unittest.main()
