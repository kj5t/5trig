"""
Yaesu FTdx-10 CAT implementation.

The FTdx-10 shares most commands with the FT-710 but adds:
  - Band-scope data (BSM/BS command family)
  - Sub-receiver control
  - Additional memory/menu commands

CAT port: USB-B → /dev/ttyUSB0 or /dev/ttyACM0.
Default baud: 38400, 8N2.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .base import (
    VFO,
    CATCommand,
    CATRadio,
    Meters,
    Mode,
    RadioEvent,
)
from .filters import IF_SHIFT_MAX, IF_SHIFT_STEP

logger = logging.getLogger(__name__)

# Number of points the FTdx-10 band scope returns per sweep
SCOPE_POINTS = 475


class FTdx10(CATRadio):
    """Yaesu FTdx-10."""

    MODEL_NAME = "FTdx-10"
    BAUD_RATE = 38400
    BYTESIZE = 8
    PARITY = "N"
    STOPBITS = 2

    def __init__(self) -> None:
        super().__init__()
        self._scope_buf: bytearray = bytearray()

    # -----------------------------------------------------------------------
    # Command builders (identical to FT-710 for core commands)
    # -----------------------------------------------------------------------

    def build_set_freq(self, vfo: VFO, hz: int) -> bytes:
        letter = "A" if vfo == VFO.A else "B"
        return f"F{letter}{hz:09d};".encode()

    def build_get_freq(self, vfo: VFO) -> CATCommand:
        letter = "A" if vfo == VFO.A else "B"
        return CATCommand(
            raw=f"F{letter};".encode(),
            reply_len=12,
            callback=self._on_freq_reply(vfo),
            priority=6,
        )

    def build_set_mode(self, mode: Mode) -> bytes:
        return f"MD0{mode.value};".encode()

    def build_get_mode(self) -> CATCommand:
        return CATCommand(
            raw=b"MD0;",
            reply_len=5,
            callback=self._on_mode_reply,
            priority=7,
        )

    def build_set_ptt(self, tx: bool) -> bytes:
        return b"TX1;" if tx else b"TX0;"

    def build_poll_commands(self) -> list[CATCommand]:
        cmds = [
            self.build_get_freq(VFO.A),
            self.build_get_freq(VFO.B),
            self.build_get_mode(),
            self._build_get_smeter(),
        ]
        # Request one scope sweep per poll if waterfall is active
        cmds.append(self._build_request_scope())
        return cmds

    def build_slow_poll_commands(self) -> list[CATCommand]:
        # SH0 = width index, IS0 = IF shift, NA0 = narrow on/off (all MAIN receiver)
        return [CATCommand(raw=raw, priority=8) for raw in (b"SH0;", b"IS0;", b"NA0;")]

    # -----------------------------------------------------------------------
    # Receive filter
    # -----------------------------------------------------------------------

    def build_set_filter_width(self, idx: int) -> bytes:
        """SH0 0 pp ; — pp = 00..23, an index into the per-mode width table."""
        return f"SH00{idx:02d};".encode()

    def build_set_if_shift(self, hz: int) -> bytes:
        """IS0 0 +/- hhhh ; — 0..1200 Hz in 20 Hz steps."""
        hz = max(-IF_SHIFT_MAX, min(IF_SHIFT_MAX, hz))
        hz = int(round(hz / IF_SHIFT_STEP)) * IF_SHIFT_STEP
        return f"IS00{'-' if hz < 0 else '+'}{abs(hz):04d};".encode()

    def build_set_narrow(self, on: bool) -> bytes:
        return b"NA01;" if on else b"NA00;"

    def set_filter_width(self, idx: int) -> None:
        idx = max(0, min(23, int(idx)))
        self.enqueue(CATCommand(raw=self.build_set_filter_width(idx), priority=3))
        self.state.filter_width_idx = idx
        self._state_changed()

    def set_if_shift(self, hz: int) -> None:
        raw = self.build_set_if_shift(hz)
        self.enqueue(CATCommand(raw=raw, priority=3))
        self.state.if_shift_hz = int(raw[5:9]) * (-1 if raw[4:5] == b"-" else 1)
        self._state_changed()

    def set_narrow(self, on: bool) -> None:
        self.enqueue(CATCommand(raw=self.build_set_narrow(on), priority=3))
        self.state.narrow = bool(on)
        self._state_changed()

    def _state_changed(self) -> None:
        """Tell listeners now, so the UI doesn't wait for the next slow poll."""
        with contextlib.suppress(RuntimeError):   # no running loop: unit tests, shutdown
            asyncio.get_running_loop().create_task(
                self._emit(RadioEvent.STATE_CHANGED, self.state.clone())
            )

    # -----------------------------------------------------------------------
    # Band scope (FTdx-10 specific)
    # -----------------------------------------------------------------------

    def _build_request_scope(self) -> CATCommand:
        """BS3; — request one scope sweep, radio replies with BSM data."""
        return CATCommand(
            raw=b"BS3;",
            reply_len=0,   # reply comes asynchronously via data_received
            priority=9,
        )

    def _build_get_smeter(self) -> CATCommand:
        return CATCommand(
            raw=b"SM0;",
            reply_len=7,
            callback=self._on_smeter_reply,
            priority=8,
        )

    # -----------------------------------------------------------------------
    # Reply parsers
    # -----------------------------------------------------------------------

    def parse_freq_reply(self, raw: bytes) -> int:
        text = raw.decode(errors="ignore").strip().rstrip(";")
        return int(text[2:])

    def parse_mode_reply(self, raw: bytes) -> Mode:
        text = raw.decode(errors="ignore").strip().rstrip(";")
        return Mode.from_code(text[3:4])

    # -----------------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------------

    def _on_freq_reply(self, vfo: VFO):
        def _cb(raw: bytes) -> None:
            try:
                hz = self.parse_freq_reply(raw)
                if vfo == VFO.A:
                    self.state.vfo_a_hz = hz
                else:
                    self.state.vfo_b_hz = hz
                asyncio.create_task(self._emit(RadioEvent.STATE_CHANGED, self.state.clone()))
            except (ValueError, IndexError):
                logger.warning("Bad freq reply: %r", raw)
        return _cb

    def _on_mode_reply(self, raw: bytes) -> None:
        try:
            self.state.mode = self.parse_mode_reply(raw)
            asyncio.create_task(self._emit(RadioEvent.STATE_CHANGED, self.state.clone()))
        except (ValueError, IndexError):
            logger.warning("Bad mode reply: %r", raw)

    def _on_smeter_reply(self, raw: bytes) -> None:
        try:
            text = raw.decode(errors="ignore").strip().rstrip(";")
            value = int(text[3:])
            self.state.meter_values[Meters.SMETER] = value / 30.0
            asyncio.create_task(self._emit(RadioEvent.STATE_CHANGED, self.state.clone()))
        except (ValueError, IndexError):
            logger.warning("Bad S-meter reply: %r", raw)

    def _on_filter_reply(self, token: str) -> None:
        """SH0pqq / IS0p+hhhh / NA0p — replies to the slow poll."""
        try:
            if token.startswith("SH"):
                self.state.filter_width_idx = int(token[4:6])
            elif token.startswith("IS"):
                sign = -1 if token[4] == "-" else 1
                self.state.if_shift_hz = sign * int(token[5:9])
            elif token.startswith("NA"):
                self.state.narrow = token[3] == "1"
            asyncio.create_task(self._emit(RadioEvent.STATE_CHANGED, self.state.clone()))
        except (ValueError, IndexError):
            logger.warning("Bad filter reply: %r", token)

    def _on_scope_data(self, raw: bytes) -> None:
        """Parse BSM scope reply.

        Format (approximate): BSM0ddd...ddd;
        where each 'ddd' is a 3-digit decimal level 0-255.
        The FTdx-10 returns SCOPE_POINTS triplets.
        """
        try:
            text = raw.decode(errors="ignore").strip().rstrip(";")
            # BSM0<n><data> — skip "BSM0" prefix and the sweep number byte
            payload = text[5:]
            levels = [int(payload[i:i+3]) for i in range(0, len(payload), 3)]
            asyncio.create_task(self._emit(RadioEvent.SCOPE_DATA, levels))
        except (ValueError, IndexError):
            logger.debug("Partial or bad scope data: %d bytes", len(raw))

    # -----------------------------------------------------------------------
    # Response dispatch
    # -----------------------------------------------------------------------

    def _dispatch_response(self, data: bytes) -> None:
        text = data.decode(errors="ignore")
        for token in text.split(";"):
            token = token.strip()
            if not token:
                continue
            raw = (token + ";").encode()
            if token.startswith("FA"):
                self._on_freq_reply(VFO.A)(raw)
            elif token.startswith("FB"):
                self._on_freq_reply(VFO.B)(raw)
            elif token.startswith("MD"):
                self._on_mode_reply(raw)
            elif token.startswith("SM"):
                self._on_smeter_reply(raw)
            elif token.startswith(("SH", "IS", "NA")):
                self._on_filter_reply(token)
            elif token.startswith("BSM"):
                self._on_scope_data(raw)
            else:
                logger.debug("Unhandled reply: %r", token)
