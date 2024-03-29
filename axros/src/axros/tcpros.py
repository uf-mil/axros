"""
Handles all connections related to TCPROS, the transport layer for ROS messages
and services. For a general overview of TCPROS, please see http://wiki.ros.org/ROS/TCPROS.

The NodeHandle handles all connections incoming through TCPROS. Upon receiving
a connection, the node handle reroutes the incoming call to the appropriate Publisher
or Subscriber node, which is then able to respond.
"""
from __future__ import annotations

import asyncio
import struct
import traceback
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from . import types


def deserialize_list(s: bytes) -> list[bytes]:
    pos = 0
    res = []
    while pos != len(s):
        (length,) = struct.unpack("<I", s[pos : pos + 4])
        if pos + 4 + length > len(s):
            raise ValueError("early end")
        res.append(s[pos + 4 : pos + 4 + length])
        pos = pos + 4 + length
    return res


def serialize_list(lst: Iterator[bytes]) -> bytes:
    return b"".join(struct.pack("<I", len(x)) + x for x in lst)


def deserialize_dict(s: bytes) -> dict[str, str]:
    res = {}
    for item in deserialize_list(s):
        key, value = item.split(b"=", 1)
        key, value = key.decode(), value.decode()
        res[key] = value
    return res


def serialize_dict(s: dict[str, str]) -> bytes:
    return serialize_list(f"{k}={v}".encode() for k, v in s.items())


async def receive_string(reader: asyncio.StreamReader) -> bytes:
    (length,) = struct.unpack("<I", await reader.readexactly(4))
    return await reader.readexactly(length)


async def receive_byte(reader: asyncio.StreamReader) -> bytes:
    return await reader.readexactly(1)


def send_string(string: bytes, writer: asyncio.StreamWriter) -> None:
    try:
        writer.write(struct.pack("<I", len(string)) + string)
    except RuntimeError:  # Emitted by uvloop when the transport is closed
        pass


def send_byte(byte: bytes, writer: asyncio.StreamWriter) -> None:
    try:
        writer.write(byte)
    except RuntimeError:  # Emitted by uvloop when the transport is closed
        pass


async def callback(
    tcpros_handlers: dict[tuple[str, str], list[types.TCPROSProtocol]],
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
):
    try:
        try:
            header = deserialize_dict(await receive_string(reader))

            async def default(
                header: dict[str, str],
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ):
                del header  # In the default case, we don't handle header
                send_string(serialize_dict(dict(error="unhandled connection")), writer)
                writer.close()
                await writer.wait_closed()

            if "service" in header:
                handlers = tcpros_handlers.get(("service", header["service"]))
                if not handlers:
                    await default(header, reader, writer)
                else:
                    for handler in handlers:
                        await handler(header, reader, writer)

            elif "topic" in header:
                handlers = tcpros_handlers.get(("topic", header["topic"]))
                if not handlers:
                    await default(header, reader, writer)
                else:
                    for handler in handlers:
                        await handler(header, reader, writer)
            else:
                send_string(
                    serialize_dict(dict(error="no topic or service name detected")),
                    writer,
                )
                writer.close()
                await writer.wait_closed()
        except asyncio.CancelledError:
            send_string(serialize_dict(dict(error="shutting down...")), writer)
    except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
        # If these exceptions are triggered, the client likely disconnected, and
        # there is no need to fulfill their request
        return
    except Exception:
        traceback.print_exc()
    writer.close()
    await writer.wait_closed()
