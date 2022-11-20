#!/usr/bin/python3


import random

import rospy
from geometry_msgs.msg import Point, PointStamped
from std_msgs.msg import Header

rospy.init_node("publish_points")

pub = rospy.Publisher("point", PointStamped)

while not rospy.is_shutdown():
    pub.publish(
        PointStamped(
            header=Header(
                stamp=rospy.Time.now(),
                frame_id="/axros_demo",
            ),
            point=Point(*[random.gauss(0, 1) for i in range(3)]),
        )
    )
    rospy.sleep(1 / 4)
