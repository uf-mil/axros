#! /usr/bin/env python3
import unittest

import axros
import rostest
from std_srvs.srv import SetBool, SetBoolRequest, SetBoolResponse


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests the subscribing and publishing functionality of axros.
    """

    async def test_basic_service(self):
        nh = axros.NodeHandle.from_argv("basic", always_default_name=True)
        await nh.setup()

        async def callback(_: SetBoolRequest) -> SetBoolResponse:
            return SetBoolResponse(True, "The response succeeded!")

        service = nh.advertise_service("basic_service", SetBool, callback)
        await service.setup()

        service_client = nh.get_service_client("basic_service", SetBool)
        response = await service_client(SetBoolRequest(False))
        self.assertIsInstance(response, SetBoolResponse)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "The response succeeded!")

        await service.shutdown()
        await nh.shutdown()

    async def test_service_bad_call(self):
        nh = axros.NodeHandle.from_argv("basic", always_default_name=True)
        with self.assertRaises(TypeError):
            async with nh:

                async def callback(_: SetBoolRequest) -> SetBoolResponse:
                    return SetBoolResponse(True, "The response succeeded!")

                service = nh.advertise_service("basic_service", SetBool, callback)
                async with service:
                    service_client = nh.get_service_client("basic_service", SetBool)
                    await service_client(1)  # type: ignore


if __name__ == "__main__":
    rostest.rosrun("axros", "test_service", ServiceTest)
    unittest.main()
