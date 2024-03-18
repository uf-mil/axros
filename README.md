<center>

# `axros`
_A simplistic Python library for using [`asyncio`](https://docs.python.org/3/library/asyncio.html) with ROS 1_

</center>
<hr />

```python
>>> import axros
>>> nh = axros.NodeHandle.from_argv("/my_special_node")
>>> await nh.setup() # or 'with nh:'
>>> await nh.set_param("my_special_param", 42)
>>> print(await nh.get_param("my_special_param"))
42
>>> from std_msgs.msg import Int32
>>> pub = await nh.advertise("count", Int32)
>>> await pub.setup()
>>> count = 0
>>> while count < 10:
...     pub.publish(Int32(count))
...     count += 1
>>> await nh.shutdown() # also shutsdown the associated publisher
```

Documentation: https://uf-mil.github.io/docs/reference/axros/index.html

Authors: Forrest Voight (`txROS`), Cameron Brown (`axros`)
