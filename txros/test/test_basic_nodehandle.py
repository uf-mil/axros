#! /usr/bin/env python3
from os import WCOREDUMP
import unittest
import rostest
import time
import txros

class BasicNodeHandleTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests basic nodehandle functionality.
    """

    nh: txros.NodeHandle

    async def asyncSetUp(self):
        self.nh = txros.NodeHandle.from_argv("basic", always_default_name = True)
        await self.nh.setup()

    async def test_name(self):
        self.assertEqual(self.nh.get_name(), "/basic")

    async def test_sleep(self):
        orig_time = time.time()
        await self.nh.sleep(2)
        self.assertAlmostEqual(time.time() - orig_time, 2, places = 1)

    async def test_sleep_value_error(self):
        for i in range(-1, -10, -1):
            with self.assertRaises(ValueError):
                await self.nh.sleep(i)

    async def test_sleep_type_error(self):
        with self.assertRaises(TypeError):
            await self.nh.sleep({1, 2, 3})

        with self.assertRaises(TypeError):
            await self.nh.sleep({'test': 'this should fail'})

        with self.assertRaises(TypeError):
            await self.nh.sleep('mil is awesome!')

        with self.assertRaises(TypeError):
            await self.nh.sleep(self.nh.sleep)

    async def test_is_running(self):
        self.assertTrue(self.nh.is_running())

    async def test_is_shutdown(self):
        self.assertFalse(self.nh.is_shutdown())

    async def test_set_and_get(self):
        await self.nh.set_param('test_param', 'test_value')
        self.assertEqual(await self.nh.get_param('test_param'), 'test_value')

    async def test_set_and_delete(self):
        await self.nh.set_param('test_param_two', 'test_value')
        await self.nh.delete_param('test_param_two')
        with self.assertRaises(txros.ROSMasterException):
            await self.nh.get_param('test_param_two')
        self.assertFalse(await self.nh.has_param('test_param_two'))

    async def test_set_has(self):
        await self.nh.set_param('test_param_three', 'test_value')
        await self.nh.has_param('test_param_three')

    async def test_param_names(self):
        params = {
            '/one': 1,
            '/two': 2,
            '/three': 3,
            '/four': 4,
        }
        for k, v in params.items():
            await self.nh.set_param(k, v)
        for k in params:
            self.assertIn(k, await self.nh.get_param_names())

        # Remove param
        await self.nh.delete_param('one')
        await self.nh.delete_param('three')
        self.assertIn('/two', await self.nh.get_param_names())
        self.assertIn('/four', await self.nh.get_param_names())
        self.assertNotIn('/one', await self.nh.get_param_names())
        self.assertNotIn('/three', await self.nh.get_param_names())

    async def asyncTearDown(self):
        await self.nh.shutdown()

if __name__ == "__main__":
    rostest.rosrun("txros", "test_basic_nodehandle", BasicNodeHandleTest)
    unittest.main()
