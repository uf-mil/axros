#! /usr/bin/env python3
import asyncio
import time
import unittest

import rospy
import rostest
import uvloop
from geometry_msgs.msg import Point
from rosgraph_msgs.msg import Clock
from std_msgs.msg import Int16

import txros


async def publish_task(pub: txros.Publisher):
    try:
        print("awaiting...")
        await asyncio.sleep(2)  # Let sub get setup
        for i in range(-10000, 10000):
            pub.publish(Int16(i))
            await asyncio.sleep(0)
        return True
    except:
        import traceback

        traceback.print_exc()


class PubSubTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests the subscribing and publishing functionality of txros.
    """

    nh: txros.NodeHandle

    async def asyncSetUp(self):
        asyncio.set_event_loop(uvloop.new_event_loop())
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

        self.nh = txros.NodeHandle.from_argv("basic", always_default_name=True)
        await self.nh.setup()

        self.pub = self.nh.advertise("clock", Clock, latching=True)
        await self.pub.setup()
        self.sub = self.nh.subscribe("clock", Clock)
        await self.sub.setup()

    async def test_subscriber(self):
        # Create message
        our_msg = Clock(rospy.Time.from_sec(time.time()))

        self.pub.publish(our_msg)
        self.assertIsNone(self.sub.get_last_message())
        self.assertIsNone(self.sub.get_last_message_time())
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

    async def test_high_rate(self):
        count = 0

        def add_one(msg):
            nonlocal count
            count += 1

        pub = self.nh.advertise("count_test", Int16, latching=True)
        await pub.setup()
        sub = self.nh.subscribe("count_test", Int16, add_one)
        async with sub:
            task = asyncio.create_task(publish_task(pub))
            await task
        await pub.shutdown()

    async def asyncTearDown(self):
        await self.nh.shutdown()


if __name__ == "__main__":
    rostest.rosrun("txros", "test_pub_sub", PubSubTest)
    unittest.main()
