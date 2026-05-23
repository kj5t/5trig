"""
Base CAT radio interface.

All Yaesu-specific implementations subclass CATRadio.  The command queue
uses asyncio to keep I/O non-blocking; the QAsyncio bridge surfaces state
changes as Qt signals via RadioState.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------


class Mode(Enum):
    LSB = "1"
    USB = "2"
    CW = "3"
    FM = "4"
    AM = "5"
    RTTY_LSB = "6"
    CW_R = "7"
    DATA_LSB = "8"
    RTTY_USB = "9"
    DATA_FM = "A"
    FM_N = "B"
    DATA_USB = "C"
    AM_N = "D"
    C4FM = "E"
    UNKNOWN = "?"

    @classmethod
    def from_code(cls, code: str) -> "Mode":
        for m in cls:
            if m.value == code:
                return m
        return cls.UNKNOWN


class Band(Enum):
    """Amateur bands in metres."""
    M160 = 160
    M80 = 80
    M60 = 60
    M40 = 40
    M30 = 30
    M20 = 20
    M17 = 17
    M15 = 15
    M12 = 12
    M10 = 10
    M6 = 6
    M2 = 2
    CM70 = 70  # 70 cm
    UNKNOWN = 0

    @classmethod
    def from_hz(cls, hz: int) -> "Band":
        mhz = hz / 1_000_000
        ranges = [
            (1.8, 2.0, cls.M160),
            (3.5, 4.0, cls.M80),
            (5.0, 5.5, cls.M60),
            (7.0, 7.3, cls.M40),
            (10.1, 10.15, cls.M30),
            (14.0, 14.35, cls.M20),
            (18.068, 18.168, cls.M17),
            (21.0, 21.45, cls.M15),
            (24.89, 24.99, cls.M12),
            (28.0, 29.7, cls.M10),
            (50.0, 54.0, cls.M6),
            (144.0, 148.0, cls.M2),
            (430.0, 440.0, cls.CM70),
        ]
        for lo, hi, band in ranges:
            if lo <= mhz <= hi:
                return band
        return cls.UNKNOWN


class VFO(Enum):
    A = "A"
    B = "B"


class Meters(Enum):
    SMETER = auto()
    POWER = auto()
    ALC = auto()
    SWR = auto()
    COMP = auto()
    VD = auto()
    ID = auto()


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class RadioState:
    """Snapshot of the radio's current state.  Mutated in-place by the CAT
    engine; observers receive a copy via signals.
    """
    vfo_a_hz: int = 0
    vfo_b_hz: int = 0
    active_vfo: VFO = VFO.A
    mode: Mode = Mode.USB
    split: bool = False
    rit_on: bool = False
    rit_hz: int = 0
    xit_on: bool = False
    xit_hz: int = 0
    ptt: bool = False
    power_pct: int = 50          # 0-100
    af_gain: int = 50            # 0-100
    rf_gain: int = 100           # 0-100
    squelch: int = 0             # 0-100
    meter_values: dict[Meters, float] = field(default_factory=dict)
    connected: bool = False
    model: str = "Unknown"

    def clone(self) -> "RadioState":
        import copy
        return copy.deepcopy(self)


@dataclass
class CATCommand:
    """A single CAT command with optional reply parsing."""
    raw: bytes
    # Expected reply length; 0 = no reply (set commands)
    reply_len: int = 0
    # Callback invoked with raw reply bytes; may be None for fire-and-forget
    callback: Callable[[bytes], None] | None = None
    priority: int = 5   # lower = higher priority
    timeout: float = 2.0


class RadioEvent(Enum):
    """Events emitted on the internal asyncio event bus."""
    CONNECTED = auto()
    DISCONNECTED = auto()
    STATE_CHANGED = auto()
    COMMAND_ERROR = auto()
    SCOPE_DATA = auto()          # payload: list[int] (level values)


# ---------------------------------------------------------------------------
# Abstract base radio
# ---------------------------------------------------------------------------

EventListener = Callable[[RadioEvent, Any], Coroutine[Any, Any, None]]


class CATRadio:
    """Abstract base for all supported Yaesu radios.

    Subclasses must implement:
      - build_set_freq(vfo, hz) -> bytes
      - build_get_freq(vfo) -> CATCommand
      - build_set_mode(mode) -> bytes
      - build_get_mode() -> CATCommand
      - parse_freq_reply(raw) -> int
      - parse_mode_reply(raw) -> Mode
      - MODEL_NAME: str
      - BAUD_RATE: int
      - BYTESIZE, PARITY, STOPBITS
    """

    MODEL_NAME: str = "Unknown"
    BAUD_RATE: int = 38400
    BYTESIZE: int = 8
    PARITY: str = "N"
    STOPBITS: int = 2       # Yaesu traditionally uses 2 stop bits
    POLL_INTERVAL: float = 0.1   # seconds between state polls

    def __init__(self) -> None:
        self.state = RadioState(model=self.MODEL_NAME)
        self._queue: asyncio.PriorityQueue[tuple[int, CATCommand]] = asyncio.PriorityQueue()
        self._listeners: list[EventListener] = []
        self._running = False
        self._serial: Any = None   # asyncio serial transport
        self._seq = 0              # tie-breaker for equal priorities

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_listener(self, fn: EventListener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: EventListener) -> None:
        self._listeners.remove(fn)

    async def connect(self, port: str) -> None:
        """Open the serial port and start the I/O loops."""
        import serial_asyncio
        loop = asyncio.get_running_loop()
        _, self._serial = await serial_asyncio.create_serial_connection(
            loop,
            lambda: _SerialProtocol(self),
            port,
            baudrate=self.BAUD_RATE,
            bytesize=self.BYTESIZE,
            parity=self.PARITY,
            stopbits=self.STOPBITS,
        )
        self._running = True
        self.state.connected = True
        await self._emit(RadioEvent.CONNECTED, None)
        asyncio.create_task(self._poll_loop())
        asyncio.create_task(self._send_loop())

    async def disconnect(self) -> None:
        self._running = False
        if self._serial:
            self._serial.close()
            self._serial = None
        self.state.connected = False
        await self._emit(RadioEvent.DISCONNECTED, None)

    def enqueue(self, cmd: CATCommand) -> None:
        """Thread-safe: schedule a command to be sent."""
        self._seq += 1
        self._queue.put_nowait((cmd.priority, self._seq, cmd))

    # Convenience helpers -----------------------------------------------

    def set_freq(self, hz: int, vfo: VFO = VFO.A) -> None:
        raw = self.build_set_freq(vfo, hz)
        self.enqueue(CATCommand(raw=raw, reply_len=0, priority=3))

    def get_freq(self, vfo: VFO = VFO.A) -> None:
        cmd = self.build_get_freq(vfo)
        self.enqueue(cmd)

    def set_mode(self, mode: Mode) -> None:
        raw = self.build_set_mode(mode)
        self.enqueue(CATCommand(raw=raw, reply_len=0, priority=3))

    def get_mode(self) -> None:
        cmd = self.build_get_mode()
        self.enqueue(cmd)

    def set_ptt(self, tx: bool) -> None:
        raw = self.build_set_ptt(tx)
        self.enqueue(CATCommand(raw=raw, reply_len=0, priority=1))

    # ------------------------------------------------------------------
    # Subclass interface (must override)
    # ------------------------------------------------------------------

    def build_set_freq(self, vfo: VFO, hz: int) -> bytes:
        raise NotImplementedError

    def build_get_freq(self, vfo: VFO) -> CATCommand:
        raise NotImplementedError

    def build_set_mode(self, mode: Mode) -> bytes:
        raise NotImplementedError

    def build_get_mode(self) -> CATCommand:
        raise NotImplementedError

    def build_set_ptt(self, tx: bool) -> bytes:
        raise NotImplementedError

    def parse_freq_reply(self, raw: bytes) -> int:
        raise NotImplementedError

    def parse_mode_reply(self, raw: bytes) -> Mode:
        raise NotImplementedError

    def build_poll_commands(self) -> list[CATCommand]:
        """Return commands to issue each poll cycle (state refresh)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        """Drain the command queue and write to serial."""
        while self._running:
            try:
                _, _seq, cmd = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            if not self._serial:
                continue

            logger.debug("TX: %s", cmd.raw.hex())
            self._serial.write(cmd.raw)

            if cmd.reply_len > 0 and cmd.callback:
                # Give the _SerialProtocol time to accumulate the reply.
                # In a real impl we'd use a future keyed by command; for
                # MVP we use a short sleep + read from a response buffer.
                await asyncio.sleep(0.05)

    async def _poll_loop(self) -> None:
        """Periodically enqueue state-refresh commands."""
        while self._running:
            for cmd in self.build_poll_commands():
                self.enqueue(cmd)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _emit(self, event: RadioEvent, payload: Any) -> None:
        for listener in self._listeners:
            try:
                await listener(event, payload)
            except Exception:
                logger.exception("Listener error for %s", event)

    def _on_data_received(self, data: bytes) -> None:
        """Called by _SerialProtocol when bytes arrive."""
        logger.debug("RX: %s", data.hex())
        # Parsing is model-specific; subclasses override or we dispatch here.
        self._dispatch_response(data)

    def _dispatch_response(self, data: bytes) -> None:
        """Subclasses may override to route raw bytes to waiting callbacks."""
        pass


# ---------------------------------------------------------------------------
# asyncio serial protocol
# ---------------------------------------------------------------------------


class _SerialProtocol(asyncio.Protocol):
    """Minimal asyncio protocol that buffers incoming bytes."""

    def __init__(self, radio: CATRadio) -> None:
        self._radio = radio
        self._buf = bytearray()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        logger.info("Serial connection established")

    def data_received(self, data: bytes) -> None:
        self._buf.extend(data)
        self._radio._on_data_received(bytes(self._buf))
        self._buf.clear()

    def connection_lost(self, exc: Exception | None) -> None:
        logger.warning("Serial connection lost: %s", exc)
        asyncio.create_task(self._radio.disconnect())
