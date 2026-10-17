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
import socket
import time
from typing import Any, Callable

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
from .protocol import configure_keepalive, decode_array, recv_msg, send_msg

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

    # Link supervision.  The shack answers pings with pongs; if nothing at all arrives
    # for HEARTBEAT_TIMEOUT s the link is declared dead (WiFi switch, sleep, roaming) and
    # reopened.  Only enforced when the server's hello advertises heartbeat support.
    HEARTBEAT_INTERVAL = 3.0
    HEARTBEAT_TIMEOUT = 10.0
    CONNECT_TIMEOUT = 5.0
    RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 10.0)   # then 10 s between attempts

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
        self.tx_audio_udp_port: int | None = None
        self.ptt_requested = False        # last PTT state we asked the shack for
        self.has_winkeyer: bool = False
        self._key_callback: Callable[[bool], None] | None = None
        self._paddle_udp_port: int | None = None
        self._paddle_sock: socket.socket | None = None
        self.auto_reconnect = True
        self.reconnecting = False         # link lost, trying to get it back
        self._heartbeat = False           # server advertised ping/pong support
        self._ping_n = 0
        self._gen = 0                     # bumped per connection; stale tasks ignore themselves
        self._hb_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._last_drop_warning = 0.0

    @property
    def link_up(self) -> bool:
        """False while the link is lost and being re-established."""
        return not self.reconnecting

    # ------------------------------------------------------------------
    # Listener bus (same interface as CATRadio)
    # ------------------------------------------------------------------

    def set_key_callback(self, cb: Callable[[bool], None] | None) -> None:
        """Register a callback for remote key state (sidetone)."""
        self._key_callback = cb

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
        self._running = True
        try:
            await self._open()
        except BaseException:
            self._running = False
            raise
        self.state.connected = True
        self._start_tasks()
        await self._emit(RadioEvent.CONNECTED, self.state)

    async def _open(self) -> None:
        """Open the TCP link and read the server's hello.  Raises if it cannot connect."""
        logger.info("Connecting to remote server %s:%d", self._host, self._port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), self.CONNECT_TIMEOUT
        )
        try:
            await self._handshake()
        except BaseException:
            self._abort_writer()
            raise

    async def _handshake(self) -> None:
        self._gen += 1
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        configure_keepalive(sock)

        # Wait for the hello handshake
        self._heartbeat = False
        try:
            hello = await asyncio.wait_for(recv_msg(self._reader), timeout=5.0)
            if hello.get("t") == "hello":
                self.state.model = hello.get("model", "Remote")
                self.audio_udp_port = hello.get("audio_udp_port")
                self.tx_audio_udp_port = hello.get("tx_audio_udp_port")
                self.has_winkeyer = hello.get("winkeyer", False)
                self._heartbeat = bool(hello.get("heartbeat"))
                self._paddle_udp_port = hello.get("paddle_udp_port")
                if self._paddle_udp_port and self._paddle_sock is None:
                    self._paddle_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                logger.info(
                    "Connected to remote radio: %s (protocol v%s, audio UDP: %s, WinKeyer: %s, paddle UDP: %s, heartbeat: %s)",
                    self.state.model,
                    hello.get("ver", "?"),
                    self.audio_udp_port or "none",
                    "yes" if self.has_winkeyer else "no",
                    self._paddle_udp_port or "none",
                    "yes" if self._heartbeat else "no",
                )
        except asyncio.TimeoutError:
            logger.warning("No hello received from server — continuing anyway")

    def _start_tasks(self) -> None:
        self._recv_task = asyncio.create_task(self._recv_loop(self._reader, self._gen))
        if self._heartbeat:
            self._hb_task = asyncio.create_task(self._heartbeat_loop(self._gen))

    async def disconnect(self) -> None:
        self._running = False
        self.reconnecting = False
        if self._paddle_sock:
            self._paddle_sock.close()
            self._paddle_sock = None
            self._paddle_udp_port = None
        for name in ("_recv_task", "_hb_task", "_reconnect_task"):
            task = getattr(self, name)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, name, None)
        if self._writer:
            try:
                self._writer.close()
                # A peer that has vanished never completes the close handshake.
                await asyncio.wait_for(self._writer.wait_closed(), 2.0)
            except Exception:
                self._abort_writer()
            self._writer = None
            self._reader = None
        self.state.connected = False
        await self._emit(RadioEvent.DISCONNECTED, self.state)

    # ------------------------------------------------------------------
    # Link supervision
    # ------------------------------------------------------------------

    def _abort_writer(self) -> None:
        try:
            if self._writer is not None:
                self._writer.transport.abort()
        except Exception:
            pass

    async def _link_lost(self, reason: str, gen: int) -> None:
        """The connection died (or went silent).  Drop it and, if enabled, reopen it."""
        if gen != self._gen or not self._running or self.reconnecting:
            return          # an older connection's leftovers, or already handling it
        logger.warning("Lost link to the shack (%s)%s", reason,
                       "; reconnecting" if self.auto_reconnect else "")
        self._abort_writer()
        self.state.connected = False
        if self._hb_task and self._hb_task is not asyncio.current_task():
            self._hb_task.cancel()
        self._hb_task = None
        if self.auto_reconnect:
            self.reconnecting = True
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        else:
            self._running = False
        await self._emit(RadioEvent.DISCONNECTED, self.state)

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while self._running:
            await asyncio.sleep(self.RECONNECT_DELAYS[min(attempt, len(self.RECONNECT_DELAYS) - 1)])
            try:
                await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                logger.info("Reconnect to %s:%d failed (%s); will retry", self._host, self._port, exc)
                continue
            break
        else:
            return
        self.reconnecting = False
        self.state.connected = True
        self._start_tasks()
        logger.info("Reconnected to the shack")
        if self.ptt_requested:
            # We may have been keyed when the link dropped; the shack cannot tell that
            # this is the same operator, so make sure the radio is released.
            self.ptt_requested = False
            self._send_nowait({"t": "set_ptt", "on": False})
        await self._emit(RadioEvent.CONNECTED, self.state)

    async def _heartbeat_loop(self, gen: int) -> None:
        while self._running and gen == self._gen:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            self._ping_n += 1
            self._send_nowait({"t": "ping", "n": self._ping_n})

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
        self.ptt_requested = tx
        self._send_nowait({"t": "set_ptt", "on": tx})

    def cw_key(self, down: bool) -> None:
        """Key down / key up for CW operation (WinKeyer / Vail / straight key)."""
        self._send_nowait({"t": "cw_key", "down": down})

    def paddle(self, dit: bool, dah: bool) -> None:
        """Send raw paddle state to the server's WinKeyer."""
        if self._paddle_sock and self._paddle_udp_port:
            b = (0x01 if dit else 0x00) | (0x02 if dah else 0x00)
            try:
                self._paddle_sock.sendto(
                    bytes([b]), (self._host, self._paddle_udp_port)
                )
            except OSError:
                pass
            return
        self._send_nowait({"t": "paddle", "dit": dit, "dah": dah})

    def cw_send_text(self, text: str) -> None:
        """Send CW message text to the server's WinKeyer."""
        self._send_nowait({"t": "cw_text", "text": text})

    def cw_send_char(self, ch: str) -> None:
        """Send a single CW character without clearing the WinKeyer buffer."""
        self._send_nowait({"t": "cw_char", "text": ch})

    def cw_stop(self) -> None:
        """Abort the current CW transmission on the server."""
        self._send_nowait({"t": "cw_stop"})

    def enqueue(self, cmd: CATCommand) -> None:
        """Send a raw CAT command to the shack radio via the server."""
        raw_str = cmd.raw.decode("ascii", errors="ignore").rstrip(";")
        self._send_nowait({"t": "cat_raw", "cmd": raw_str})

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _recv_loop(self, reader: asyncio.StreamReader, gen: int) -> None:
        """Continuously receive and dispatch frames from the server."""
        reason = "connection closed"
        try:
            while self._running and gen == self._gen:
                timeout = self.HEARTBEAT_TIMEOUT if self._heartbeat else None
                try:
                    msg = await asyncio.wait_for(recv_msg(reader), timeout)
                except asyncio.TimeoutError:
                    reason = f"nothing received for {timeout:g} s"
                    break
                await self._handle_msg(msg)
            else:
                return      # superseded or shut down; nothing to report
        except (asyncio.IncompleteReadError, ConnectionResetError, EOFError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Remote receive error: %s", exc)
            reason = f"receive error: {exc}"
        await self._link_lost(reason, gen)

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

        elif t == "cw_key_state":
            down = bool(msg["down"])
            logger.info("cw_key_state received: down=%s, callback=%s", down, self._key_callback is not None)
            if self._key_callback:
                try:
                    self._key_callback(down)
                except Exception as exc:
                    logger.error("key callback error: %s", exc)

        elif t == "hello":
            self.state.model = msg.get("model", self.state.model)

        elif t == "pong":
            pass        # any frame, including this one, proves the link is alive

        else:
            logger.debug("Unknown server message: %r", t)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_nowait(self, msg: dict) -> None:
        """Fire-and-forget: schedule a send without awaiting."""
        if not self._running:
            return
        if self._writer is None or self.reconnecting:
            # Not queued: replaying a stale frequency or PTT request later would be worse.
            now = time.monotonic()
            if now - self._last_drop_warning > 5.0:
                self._last_drop_warning = now
                logger.warning("Link to the shack is down; dropped command %r", msg.get("t"))
            return
        gen = self._gen
        try:
            task = asyncio.ensure_future(send_msg(self._writer, msg))
        except RuntimeError:
            return
        task.add_done_callback(lambda t: self._send_done(t, gen))

    def _send_done(self, task: asyncio.Future, gen: int) -> None:
        if task.cancelled():
            return
        exc = task.exception()         # also marks it retrieved
        if exc is not None:
            asyncio.ensure_future(self._link_lost(f"send failed: {exc}", gen))

    async def _emit(self, event: RadioEvent, payload: Any) -> None:
        for listener in self._listeners:
            try:
                await listener(event, payload)
            except Exception:
                logger.exception("Remote listener error for %s", event)
