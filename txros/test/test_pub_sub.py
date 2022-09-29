#! /usr/bin/env python3
import asyncio
import time
import unittest

import cv2
import numpy as np
import rospy
import rostest
import uvloop
from cv_bridge import CvBridge
from geometry_msgs.msg import Point
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image
from std_msgs.msg import Int16

import txros


async def publish_task(pub: txros.Publisher):
    try:
        await asyncio.sleep(2)  # Let sub get setup
        for i in range(0, 20000):
            pub.publish(Int16(i))
            await asyncio.sleep(0)
        return True
    except:
        import traceback

        traceback.print_exc()


async def publish_task_large(pub: txros.Publisher):
    try:
        await asyncio.sleep(2)  # Let sub get setup
        bridge = CvBridge()
        for i in range(0, 20000):
            new_img = np.zeros((100, 100, 3))
            img_msg = bridge.cv2_to_imgmsg(new_img)
            pub.publish(img_msg)
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
        sub = self.nh.subscribe("count_test", Int16, add_one)
        async with pub, sub:
            task = asyncio.create_task(publish_task(pub))
            await task
        self.assertEqual(count, 20000)

    async def test_high_rate_large(self):
        count = 0

        def add_one(msg):
            nonlocal count
            count += 1

        pub = self.nh.advertise("img_test", Image, latching=True)
        sub = self.nh.subscribe("img_test", Image, add_one)
        async with pub, sub:
            task = asyncio.create_task(publish_task_large(pub))
            await task
            while sub.recently_read():
                await asyncio.sleep(0.1)
        self.assertEqual(count, 20000)

    async def asyncTearDown(self):
        await self.nh.shutdown()


if __name__ == "__main__":
    rostest.rosrun("txros", "test_pub_sub", PubSubTest)
    unittest.main()
