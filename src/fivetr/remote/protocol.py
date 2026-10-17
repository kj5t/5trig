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
import socket
import struct

import numpy as np

_HEADER = struct.Struct(">I")   # 4-byte big-endian uint32
PROTOCOL_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024    # 4 MB safety cap

# TCP keepalive: probe an idle link after KEEPALIVE_IDLE s, every KEEPALIVE_INTERVAL s,
# give up after KEEPALIVE_COUNT misses.  USER_TIMEOUT_MS bounds how long written data may
# sit unacknowledged (Linux), so a peer that vanished (WiFi switch, sleep) errors out
# instead of leaving the socket "established" forever.
KEEPALIVE_IDLE = 10
KEEPALIVE_INTERVAL = 5
KEEPALIVE_COUNT = 3
USER_TIMEOUT_MS = 20_000


def configure_keepalive(sock: socket.socket | None) -> None:
    """Make the OS notice a dead peer.  Best effort: options differ per platform."""
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_IDLE)
        elif hasattr(socket, "TCP_KEEPALIVE"):                      # macOS
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, KEEPALIVE_IDLE)
        if hasattr(socket, "TCP_KEEPINTVL"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL)
        if hasattr(socket, "TCP_KEEPCNT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_COUNT)
        if hasattr(socket, "TCP_USER_TIMEOUT"):                     # Linux
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, USER_TIMEOUT_MS)
        if hasattr(sock, "ioctl") and hasattr(socket, "SIO_KEEPALIVE_VALS"):   # Windows
            sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                       (1, KEEPALIVE_IDLE * 1000, KEEPALIVE_INTERVAL * 1000))
    except OSError:
        pass


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
