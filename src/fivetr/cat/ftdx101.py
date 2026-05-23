"""
Yaesu FTdx-101D / FTdx-101MP CAT implementation.

The FTdx-101 has a dual-receiver architecture (Main / Sub) and exposes
richer scope data.  Key differences from FTdx-10:
  - Main/Sub VFOs instead of A/B in some contexts
  - Extended scope command set
  - Additional AGC, preamp, and attenuator controls
  - Power levels up to 200W (MP) vs 100W (D)

CAT port: USB-B → /dev/ttyUSB0 or /dev/ttyACM0.
Default baud: 38400, 8N2.
"""

from __future__ import annotations

import asyncio
import logging

from .base import (
    CATCommand,
    CATRadio,
    Meters,
    Mode,
    RadioEvent,
    VFO,
)

logger = logging.getLogger(__name__)

SCOPE_POINTS = 640  # FTdx-101 returns more points


class FTdx101(CATRadio):
    """Yaesu FTdx-101D / FTdx-101MP."""

    MODEL_NAME = "FTdx-101"
    BAUD_RATE = 38400
    BYTESIZE = 8
    PARITY = "N"
    STOPBITS = 2

    # FTdx-101 specific: AGC modes
    AGC_FAST = "F"
    AGC_MID = "M"
    AGC_SLOW = "S"
    AGC_AUTO = "A"

    def __init__(self) -> None:
        super().__init__()
        self.sub_vfo_hz: int = 0
        self.sub_mode: Mode = Mode.USB

    # -----------------------------------------------------------------------
    # Command builders
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
        return [
            self.build_get_freq(VFO.A),
            self.build_get_freq(VFO.B),
            self.build_get_mode(),
            self._build_get_smeter(),
            self._build_request_scope(),
        ]

    # -----------------------------------------------------------------------
    # FTdx-101 specific commands
    # -----------------------------------------------------------------------

    def build_set_agc(self, agc_mode: str) -> bytes:
        """GT0x; — set AGC mode (F/M/S/A)."""
        modes = {"F": "1", "M": "2", "S": "3", "A": "4"}
        code = modes.get(agc_mode, "3")
        return f"GT0{code};".encode()

    def build_set_preamp(self, level: int) -> bytes:
        """PA0n; — preamp (0=off, 1=AMP1, 2=AMP2)."""
        return f"PA0{level};".encode()

    def build_set_attenuator(self, on: bool) -> bytes:
        """RA01; / RA00;"""
        return b"RA01;" if on else b"RA00;"

    def _build_get_smeter(self) -> CATCommand:
        return CATCommand(
            raw=b"SM0;",
            reply_len=7,
            callback=self._on_smeter_reply,
            priority=8,
        )

    def _build_request_scope(self) -> CATCommand:
        """BS3; — request scope sweep (identical command, richer data)."""
        return CATCommand(raw=b"BS3;", reply_len=0, priority=9)

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
                asyncio.create_task(
                    self._emit(RadioEvent.STATE_CHANGED, self.state.clone())
                )
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

    def _on_scope_data(self, raw: bytes) -> None:
        try:
            text = raw.decode(errors="ignore").strip().rstrip(";")
            payload = text[5:]
            levels = [int(payload[i:i+3]) for i in range(0, len(payload), 3)]
            asyncio.create_task(self._emit(RadioEvent.SCOPE_DATA, levels))
        except (ValueError, IndexError):
            logger.debug("Partial scope data: %d bytes", len(raw))

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
            elif token.startswith("BSM"):
                self._on_scope_data(raw)
            else:
                logger.debug("Unhandled reply: %r", token)
