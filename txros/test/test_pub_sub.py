#! /usr/bin/env python3
import unittest
import asyncio
import rospy
import rostest
import time
import txros
from rosgraph_msgs.msg import Clock
from geometry_msgs.msg import Point

class PubSubTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests the subscribing and publishing functionality of txros.
    """

    nh: txros.NodeHandle

    async def asyncSetUp(self):
        self.nh = txros.NodeHandle.from_argv("basic", always_default_name = True)
        await self.nh.setup()

        self.pub = self.nh.advertise("clock", Clock, latching = True)
        await self.pub.setup()
        self.sub = self.nh.subscribe("clock", Clock)
        await self.sub.setup()

    async def test_subscriber(self):
        # Create message
        our_msg = Clock(rospy.Time.from_sec(time.time()))

        self.pub.publish(our_msg)
        next_msg = await self.sub.get_next_message()
        self.assertEqual(next_msg, our_msg)
        self.assertEqual(self.sub.get_last_message(), our_msg)

        self.assertTrue(self.pub.get_connections())

    async def test_subscriber_running(self):
        self.assertTrue(self.sub.is_running())
        self.assertTrue(self.pub.is_running())

    async def test_publish_bad(self):
        with self.assertRaises(TypeError):
            self.pub.publish(Point(1, 2, 3))

    async def test_message_type(self):
        self.assertEqual(self.pub.message_type, Clock)

    async def test_is_running(self):
        self.assertTrue(self.pub.is_running())
        self.assertTrue(self.sub.is_running())

    async def asyncTearDown(self):
        await self.nh.shutdown()

if __name__ == "__main__":
    rostest.rosrun("txros", "test_pub_sub", PubSubTest)
    unittest.main()
