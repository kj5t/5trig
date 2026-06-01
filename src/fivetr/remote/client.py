"""
RemoteRadio — CATRadio-compatible interface over a TCP connection.

Drop-in replacement for FT710 / FTdx10 / FTdx101 when running in remote
mode.  MainWindow uses it exactly like a local radio; it just sends JSON
frames to the shack server instead of serial bytes.

State is never polled — the server pushes updates whenever the radio
changes, so get_freq() / get_mode() are no-ops here.

CW keying
---------
Call ``cw_key(down=True/False)`` to send dit/dah elements to the shack
radio.  On the server side this asserts/releases PTT in CW mode.
WinKeyer or Vail adapters on the remote machine should call this method.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from ..cat.base import (
    CATCommand,
    CATRadio,
    Meters,
    Mode,
    RadioEvent,
    RadioState,
    VFO,
)
from .protocol import decode_array, recv_msg, send_msg

logger = logging.getLogger(__name__)


class RemoteRadio:
    """CAT interface that talks to a RemoteServer over TCP.

    Parameters
    ----------
    host:
        IP address or hostname of the shack machine.
    port:
        TCP port the RemoteServer is listening on (default 5040).
    """

    MODEL_NAME = "Remote"

    def __init__(self, host: str, port: int = 5040) -> None:
        self._host = host
        self._port = port
        self.state = RadioState(model="Remote")
        self._listeners: list = []
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._recv_task: asyncio.Task | None = None
        # Set after the hello handshake if the server offers audio streaming
        self.audio_udp_port: int | None = None

    # ------------------------------------------------------------------
    # Listener bus (same interface as CATRadio)
    # ------------------------------------------------------------------

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, port: str | None = None) -> None:
        """Open TCP connection to the shack server.

        The ``port`` argument is ignored (it's the serial port for local
        radios; remote host/port come from the constructor).
        """
        logger.info("Connecting to remote server %s:%d", self._host, self._port)
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        self._running = True
        self.state.connected = True

        # Wait for the hello handshake
        try:
            hello = await asyncio.wait_for(recv_msg(self._reader), timeout=5.0)
            if hello.get("t") == "hello":
                self.state.model = hello.get("model", "Remote")
                self.audio_udp_port = hello.get("audio_udp_port")
                logger.info(
                    "Connected to remote radio: %s (protocol v%s, audio UDP: %s)",
                    self.state.model,
                    hello.get("ver", "?"),
                    self.audio_udp_port or "none",
                )
        except asyncio.TimeoutError:
            logger.warning("No hello received from server — continuing anyway")

        self._recv_task = asyncio.create_task(self._recv_loop())
        await self._emit(RadioEvent.CONNECTED, self.state)

    async def disconnect(self) -> None:
        self._running = False
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        self.state.connected = False
        await self._emit(RadioEvent.DISCONNECTED, self.state)

    # ------------------------------------------------------------------
    # CAT command methods (same surface as CATRadio)
    # ------------------------------------------------------------------

    def set_freq(self, hz: int, vfo: VFO = VFO.A) -> None:
        self._send_nowait({"t": "set_freq", "vfo": vfo.value, "hz": hz})

    def get_freq(self, vfo: VFO = VFO.A) -> None:
        pass  # server pushes state automatically on every poll

    def set_mode(self, mode: Mode) -> None:
        self._send_nowait({"t": "set_mode", "code": mode.value})

    def get_mode(self) -> None:
        pass

    def set_ptt(self, tx: bool) -> None:
        self._send_nowait({"t": "set_ptt", "on": tx})

    def cw_key(self, down: bool) -> None:
        """Key down / key up for CW operation (WinKeyer / Vail / straight key)."""
        self._send_nowait({"t": "cw_key", "down": down})

    def enqueue(self, cmd: CATCommand) -> None:
        """Send a raw CAT command to the shack radio via the server."""
        raw_str = cmd.raw.decode("ascii", errors="ignore").rstrip(";")
        self._send_nowait({"t": "cat_raw", "cmd": raw_str})

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _recv_loop(self) -> None:
        """Continuously receive and dispatch frames from the server."""
        try:
            while self._running and self._reader:
                msg = await recv_msg(self._reader)
                await self._handle_msg(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, EOFError):
            logger.info("Connection to remote server closed")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Remote receive error: %s", exc)
        finally:
            if self._running:
                # Unexpected disconnect
                self._running = False
                self.state.connected = False
                await self._emit(RadioEvent.DISCONNECTED, self.state)

    async def _handle_msg(self, msg: dict) -> None:
        t = msg.get("t")

        if t == "state":
            s = msg["s"]
            self.state.vfo_a_hz = s.get("vfo_a", self.state.vfo_a_hz)
            self.state.vfo_b_hz = s.get("vfo_b", self.state.vfo_b_hz)
            self.state.mode = Mode.from_code(s.get("mode", self.state.mode.value))
            self.state.split = s.get("split", self.state.split)
            self.state.rit_on = s.get("rit_on", self.state.rit_on)
            self.state.rit_hz = s.get("rit_hz", self.state.rit_hz)
            self.state.xit_on = s.get("xit_on", self.state.xit_on)
            self.state.ptt = s.get("ptt", self.state.ptt)
            self.state.model = s.get("model", self.state.model)
            # Meters
            raw_meters = s.get("meters", {})
            for m in Meters:
                if m.value in raw_meters:
                    self.state.meter_values[m] = raw_meters[m.value]
            await self._emit(RadioEvent.STATE_CHANGED, self.state.clone())

        elif t == "scope":
            db = decode_array(msg["d"])
            await self._emit(RadioEvent.SCOPE_DATA, db)

        elif t == "hello":
            self.state.model = msg.get("model", self.state.model)

        else:
            logger.debug("Unknown server message: %r", t)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_nowait(self, msg: dict) -> None:
        """Fire-and-forget: schedule a send without awaiting."""
        if self._writer is None or not self._running:
            return
        try:
            asyncio.ensure_future(send_msg(self._writer, msg))
        except RuntimeError:
            pass

    async def _emit(self, event: RadioEvent, payload: Any) -> None:
        for listener in self._listeners:
            try:
                await listener(event, payload)
            except Exception:
                logger.exception("Remote listener error for %s", event)
