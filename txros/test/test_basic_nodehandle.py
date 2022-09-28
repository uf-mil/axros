#! /usr/bin/env python3
import time
import unittest
from os import WCOREDUMP

import rostest

import txros


class BasicNodeHandleTest(unittest.IsolatedAsyncioTestCase):
    """
    Tests basic nodehandle functionality.
    """

    nh: txros.NodeHandle

    async def asyncSetUp(self):
        self.nh = txros.NodeHandle.from_argv("basic", always_default_name=True)
        await self.nh.setup()

    async def test_name(self):
        self.assertEqual(self.nh.get_name(), "/basic")

    async def test_sleep(self):
        orig_time = time.time()
        await self.nh.sleep(2)
        self.assertAlmostEqual(time.time() - orig_time, 2, places=1)

    async def test_sleep_value_error(self):
        for i in range(-1, -10, -1):
            with self.assertRaises(ValueError):
                await self.nh.sleep(i)

    async def test_sleep_type_error(self):
        with self.assertRaises(TypeError):
            await self.nh.sleep({1, 2, 3})

        with self.assertRaises(TypeError):
            await self.nh.sleep({"test": "this should fail"})

        with self.assertRaises(TypeError):
            await self.nh.sleep("mil is awesome!")

        with self.assertRaises(TypeError):
            await self.nh.sleep(self.nh.sleep)

    async def test_is_running(self):
        self.assertTrue(self.nh.is_running())

    async def test_is_shutdown(self):
        self.assertFalse(self.nh.is_shutdown())

    async def test_set_and_get(self):
        await self.nh.set_param("test_param", "test_value")
        self.assertEqual(await self.nh.get_param("test_param"), "test_value")
        await self.nh.set_param("test_param", "new_test_value")
        self.assertEqual(await self.nh.get_param("test_param"), "new_test_value")

    async def test_complicated_set(self):
        await self.nh.set_param("bool", True)
        self.assertTrue(self.nh.get_param("bool"))

        await self.nh.set_param("int", 3701)
        self.assertEqual(await self.nh.get_param("int"), 3701)

        await self.nh.set_param("float", 47.44)
        self.assertEqual(await self.nh.get_param("float"), 47.44)

        await self.nh.set_param("list", ["a", 123, [1, 2, 3], {"what?": "yes!"}])
        self.assertEqual(
            await self.nh.get_param("list"), ["a", 123, [1, 2, 3], {"what?": "yes!"}]
        )

        await self.nh.set_param("tuple", ("a", 123, [1, 2, 3], {"what?": "yes!"}))
        self.assertEqual(
            tuple(await self.nh.get_param("tuple")),
            ("a", 123, [1, 2, 3], {"what?": "yes!"}),
        )

        await self.nh.set_param("dict", {"bool": True, "str": "MIL", "list": [1, 2, 3]})
        self.assertEqual(
            await self.nh.get_param("dict"),
            {"bool": True, "str": "MIL", "list": [1, 2, 3]},
        )

        keys = ["bool", "int", "float", "list", "tuple", "dict"]
        for k in keys:
            await self.nh.delete_param(k)

    async def test_set_int_range(self):
        for i in range(0, 32):
            await self.nh.set_param(f"positive_{i}", 2**i - 1)
            self.assertEqual(await self.nh.get_param(f"positive_{i}"), 2**i - 1)
            await self.nh.delete_param(f"positive_{i}")

            await self.nh.set_param(f"negative_{i}", -(2**i - 1))
            self.assertEqual(await self.nh.get_param(f"negative_{i}"), -(2**i - 1))
            await self.nh.delete_param(f"negative_{i}")

    async def test_invalid_set(self):
        with self.assertRaises(TypeError):
            await self.nh.set_param(2, 1)

        with self.assertRaises(TypeError):
            await self.nh.set_param({"a": 2}, 1)

        with self.assertRaises(TypeError):
            await self.nh.set_param(self, 1)

    async def test_invalid_get(self):
        with self.assertRaises(TypeError):
            await self.nh.get_param(2)

        with self.assertRaises(TypeError):
            await self.nh.get_param({"a": 2})

        with self.assertRaises(TypeError):
            await self.nh.get_param(self)

    async def test_invalid_delete(self):
        with self.assertRaises(TypeError):
            await self.nh.delete_param(2)

        with self.assertRaises(TypeError):
            await self.nh.delete_param({"a": 2})

        with self.assertRaises(TypeError):
            await self.nh.delete_param(self)

    async def test_invalid_has(self):
        with self.assertRaises(TypeError):
            await self.nh.has_param(2)

        with self.assertRaises(TypeError):
            await self.nh.has_param({"a": 2})

        with self.assertRaises(TypeError):
            await self.nh.has_param(self)

    async def test_set_and_delete(self):
        await self.nh.set_param("test_param_two", "test_value")
        await self.nh.delete_param("test_param_two")
        with self.assertRaises(txros.ROSMasterError):
            await self.nh.get_param("test_param_two")
        self.assertFalse(await self.nh.has_param("test_param_two"))

    async def test_delete_again(self):
        await self.nh.set_param("delete_me", 1)
        await self.nh.delete_param("delete_me")
        self.assertFalse(await self.nh.has_param("delete_me"))
        with self.assertRaises(txros.ROSMasterError):
            await self.nh.delete_param("delete_me")

    async def test_set_has(self):
        await self.nh.set_param("test_param_three", "test_value")
        self.assertTrue(await self.nh.has_param("test_param_three"))

    async def test_param_names(self):
        params = {
            "/one": 1,
            "/two": 2,
            "/three": 3,
            "/four": 4,
        }
        for k, v in params.items():
            await self.nh.set_param(k, v)
        for k in params:
            self.assertIn(k, await self.nh.get_param_names())

        # Remove param
        await self.nh.delete_param("one")
        await self.nh.delete_param("three")
        self.assertIn("/two", await self.nh.get_param_names())
        self.assertIn("/four", await self.nh.get_param_names())
        self.assertNotIn("/one", await self.nh.get_param_names())
        self.assertNotIn("/three", await self.nh.get_param_names())

    async def asyncTearDown(self):
        await self.nh.shutdown()


if __name__ == "__main__":
    rostest.rosrun("txros", "test_basic_nodehandle", BasicNodeHandleTest)
    unittest.main()
