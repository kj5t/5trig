"""
DX Spider telnet cluster client.

Connects to a DX Spider node, logs in with callsign, and emits
DXSpot objects via registered callbacks.

Example nodes:
  dxc.nc7j.com:7373
  dx.wb9z.net:7300
  cluster.dl9gtb.de:8000
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from .spot import DXSpot

logger = logging.getLogger(__name__)

SpotCallback = Callable[[DXSpot], None]
RawLineCallback = Callable[[str], None]


class ClusterClient:
    """Async DX cluster telnet client."""

    RECONNECT_DELAY = 30   # seconds before reconnect attempt

    def __init__(
        self,
        host: str,
        port: int,
        callsign: str,
    ) -> None:
        self.host = host
        self.port = port
        self.callsign = callsign.upper()

        self._spot_callbacks: list[SpotCallback] = []
        self._raw_callbacks: list[RawLineCallback] = []
        self._running = False
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_spot_callback(self, cb: SpotCallback) -> None:
        self._spot_callbacks.append(cb)

    def add_raw_callback(self, cb: RawLineCallback) -> None:
        self._raw_callbacks.append(cb)

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._writer:
            self._writer.close()

    async def send(self, text: str) -> None:
        """Send a raw command to the cluster node."""
        if self._writer and not self._writer.is_closing():
            self._writer.write((text + "\r\n").encode())
            await self._writer.drain()

    async def set_filter(self, band: str | None = None, mode: str | None = None) -> None:
        """Ask the cluster to filter spots server-side (node-dependent)."""
        if band:
            await self.send(f"set/dx/filter band {band}")
        if mode:
            await self.send(f"set/dx/filter mode {mode}")

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._connect_and_read()
            except (ConnectionRefusedError, OSError, TimeoutError) as e:
                logger.warning("Cluster connection failed: %s — retry in %ds", e, self.RECONNECT_DELAY)
            except Exception:
                logger.exception("Cluster client error")

            if self._running:
                await asyncio.sleep(self.RECONNECT_DELAY)

    async def _connect_and_read(self) -> None:
        logger.info("Connecting to cluster %s:%d", self.host, self.port)
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer
        self._connected = True
        logger.info("Cluster connected")

        try:
            async for line in self._read_lines(reader):
                line = line.strip()
                if not line:
                    continue

                # Emit raw line
                for cb in self._raw_callbacks:
                    try:
                        cb(line)
                    except Exception:
                        logger.exception("Raw callback error")

                # Login prompt
                if "login:" in line.lower() or "callsign:" in line.lower():
                    writer.write((self.callsign + "\r\n").encode())
                    await writer.drain()
                    continue

                # Parse spot
                spot = DXSpot.parse(line)
                if spot:
                    for cb in self._spot_callbacks:
                        try:
                            cb(spot)
                        except Exception:
                            logger.exception("Spot callback error")

        finally:
            writer.close()
            self._connected = False
            self._writer = None

    @staticmethod
    async def _read_lines(reader: asyncio.StreamReader):
        """Yield decoded lines, handling telnet option negotiations."""
        buf = bytearray()
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=120)
            except TimeoutError:
                # Send a keep-alive
                yield ""
                continue

            if not chunk:
                break

            # Strip telnet IAC sequences (0xFF prefix)
            clean = bytearray()
            i = 0
            while i < len(chunk):
                b = chunk[i]
                if b == 0xFF and i + 2 < len(chunk):
                    i += 3   # skip IAC + cmd + option
                    continue
                clean.append(b)
                i += 1

            buf.extend(clean)
            while b"\n" in buf:
                idx = buf.index(b"\n")
                line = buf[:idx].decode("utf-8", errors="replace").rstrip("\r")
                buf = buf[idx + 1:]
                yield line
