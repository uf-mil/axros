#!/usr/bin/python3

import axros
import rospy
from tf import transformations

rospy.init_node("publish_tf_rospy")

tf_broadcaster = axros.TransformBroadcaster()

while not rospy.is_shutdown():
    t = rospy.Time.now()
    tf_broadcaster.sendTransform(
        [1, 2, 3],
        transformations.quaternion_about_axis(t.to_sec(), [0, 0, 1]),
        t,
        "/child",
        "/parent",
    )
    rospy.sleep(0.1)
