"""
5trig remote wire protocol helpers.

Frame format:
  [ 4-byte big-endian uint32 = payload length ][ UTF-8 JSON payload ]

send_msg / recv_msg handle framing.  encode_array / decode_array convert
numpy float32 arrays to/from base64 for embedding in JSON messages.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct

import numpy as np

_HEADER = struct.Struct(">I")   # 4-byte big-endian uint32
PROTOCOL_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024    # 4 MB safety cap


async def send_msg(writer: asyncio.StreamWriter, msg: dict) -> None:
    """Serialise *msg* and write a length-prefixed frame to *writer*."""
    payload = json.dumps(msg, separators=(",", ":")).encode()
    writer.write(_HEADER.pack(len(payload)) + payload)
    await writer.drain()


async def recv_msg(reader: asyncio.StreamReader) -> dict:
    """Read one length-prefixed frame from *reader* and return the parsed dict."""
    header = await reader.readexactly(4)
    length = _HEADER.unpack(header)[0]
    if length > MAX_FRAME:
        raise ValueError(f"Frame too large: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload)


def encode_array(arr: np.ndarray) -> str:
    """Encode a float32 ndarray as a base64 string for JSON transport."""
    return base64.b64encode(arr.astype(np.float32).tobytes()).decode("ascii")


def decode_array(s: str) -> np.ndarray:
    """Decode a base64 string back to a float32 ndarray."""
    return np.frombuffer(base64.b64decode(s), dtype=np.float32).copy()
