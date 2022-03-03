#!/usr/bin/python

from twisted.internet import defer

import txros
from txros import util

from geometry_msgs.msg import PointStamped
from roscpp.srv import GetLoggers, GetLoggersRequest


@util.cancellableInlineCallbacks
def main():
    nh = yield txros.NodeHandle.from_argv('testnode', anonymous=True)
    
    pub = nh.advertise('point2', PointStamped, latching=True)
    def cb(msg):
        pub.publish(msg)
    sub = nh.subscribe('point', PointStamped, cb)
    
    @util.cancellableInlineCallbacks
    def task1():
        msg = yield sub.get_next_message()
        print msg
    task1()
    
    @util.cancellableInlineCallbacks
    def task2():
        while True:
            yield nh.sleep(1)
            x = yield nh.get_param('/')
    task2()
    
    @util.cancellableInlineCallbacks
    def task3():
        while nh.is_running():
            pub = nh.advertise('~point3', PointStamped, latching=True)
            yield nh.sleep(0.001)
            yield pub.shutdown()
    task3()
    
    @util.cancellableInlineCallbacks
    def task4():
        print (yield nh.get_service_client('/rosout/get_loggers', GetLoggers)(GetLoggersRequest()))
    task4()
    
    yield defer.Deferred() # never exit
util.launch_main(main)
