#!/usr/bin/python3

import random
from twisted.internet import defer

import txros
from txros import util, action

from actionlib.msg import TestAction, TestGoal


@util.cancellableInlineCallbacks
def main():
    nh = yield txros.NodeHandle.from_argv("action_test_node", anonymous=True)

    ac = action.ActionClient(nh, "test_action", TestAction)

    x = random.randrange(1000)
    goal_manager = ac.send_goal(
        TestGoal(
            goal=x,
        )
    )
    print("sent goal")

    while True:
        result, index = yield defer.DeferredList(
            [goal_manager.get_feedback(), goal_manager.get_result()],
            fireOnOneCallback=True,
            fireOnOneErrback=True,
        )

        if index == 0:  # feedback
            print("got feedback", result.feedback)
        else:
            assert index == 1  # result

            assert result.result == x + 1000, result.result
            print("success")

            break

util.launch_main(main)
