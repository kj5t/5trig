"""
Yaesu FT-710 AESS CAT implementation.

CAT port: USB-B (appears as /dev/ttyUSB0 or /dev/ttyACM0 on Linux).
Default baud: 38400, 8N2.  Change in Menu > CAT RATE.

Command reference: FT-710 Operating Manual Appendix — CAT Operation.
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


class FT710(CATRadio):
    """Yaesu FT-710 AESS."""

    MODEL_NAME = "FT-710"
    BAUD_RATE = 38400
    BYTESIZE = 8
    PARITY = "N"
    STOPBITS = 2

    # -----------------------------------------------------------------------
    # Command builders
    # -----------------------------------------------------------------------

    def build_set_freq(self, vfo: VFO, hz: int) -> bytes:
        """FA/FB  ppppppppp;  — 9-digit zero-padded Hz, no MHz/kHz prefix."""
        letter = "A" if vfo == VFO.A else "B"
        return f"F{letter}{hz:09d};".encode()

    def build_get_freq(self, vfo: VFO) -> CATCommand:
        letter = "A" if vfo == VFO.A else "B"
        cmd = f"F{letter};".encode()
        return CATCommand(
            raw=cmd,
            reply_len=12,  # "FA000014074000;" = 15 bytes
            callback=self._on_freq_reply(vfo),
            priority=6,
        )

    def build_set_mode(self, mode: Mode) -> bytes:
        return f"MD0{mode.value};".encode()

    def build_get_mode(self) -> CATCommand:
        return CATCommand(
            raw=b"MD0;",
            reply_len=5,   # "MD01;" = 5 bytes
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
        ]

    # -----------------------------------------------------------------------
    # Reply parsers
    # -----------------------------------------------------------------------

    def parse_freq_reply(self, raw: bytes) -> int:
        """FA000014074000; → 14074000"""
        text = raw.decode(errors="ignore").strip().rstrip(";")
        return int(text[2:])   # skip "FA" or "FB"

    def parse_mode_reply(self, raw: bytes) -> Mode:
        """MD01; → Mode.LSB"""
        text = raw.decode(errors="ignore").strip().rstrip(";")
        return Mode.from_code(text[3:4])

    # -----------------------------------------------------------------------
    # Meter / misc
    # -----------------------------------------------------------------------

    def _build_get_smeter(self) -> CATCommand:
        return CATCommand(
            raw=b"SM0;",
            reply_len=7,   # "SM00015;" = 8 bytes
            callback=self._on_smeter_reply,
            priority=8,
        )

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
            value = int(text[3:])   # "SM00015" → 15
            # FT-710: S-meter range 0-30 (S9 = 15, S9+60 = 30)
            self.state.meter_values[Meters.SMETER] = value / 30.0
            asyncio.create_task(self._emit(RadioEvent.STATE_CHANGED, self.state.clone()))
        except (ValueError, IndexError):
            logger.warning("Bad S-meter reply: %r", raw)

    # -----------------------------------------------------------------------
    # Response dispatch — FT-710 terminates replies with ";"
    # -----------------------------------------------------------------------

    def _dispatch_response(self, data: bytes) -> None:
        """Split on ';' and route each complete reply."""
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
            else:
                logger.debug("Unhandled reply token: %r", token)
