"""
K1EL WinKeyer USB v2/v3 serial driver.

Opens the WinKeyer in host mode and provides:
  • key_immediate(down)  — assert/release the KEY output directly
  • set_speed(wpm)       — change keying speed
  • send_text(msg)       — queue a CW message (WinKeyer does the timing)
  • paddle(dit, dah)     — emulate paddle input for the WinKeyer's
                           built-in iambic keyer

For remote operation the primary interface is ``key_immediate`` (the
remote iambic keyer has already done the timing) or ``paddle`` (let
the WinKeyer's iambic engine do the timing locally for lowest latency).

WinKeyer host-mode protocol reference:
  https://k1el.tripod.com/files/WK3_Datasheet_v1.3.pdf
"""

from __future__ import annotations

import logging
import threading
import time

import serial

logger = logging.getLogger(__name__)

# WinKeyer host-mode admin commands (first byte = 0x00, second = sub-cmd)
_ADMIN = 0x00
_ADMIN_OPEN = 0x02
_ADMIN_CLOSE = 0x03
_ADMIN_ECHO = 0x04

# WinKeyer immediate commands (single byte prefix, then data)
_CMD_SPEED = 0x02          # Set WPM (1 byte follows)
_CMD_CLEAR_BUFFER = 0x0A
_CMD_KEY_IMMEDIATE = 0x0B  # 1 byte follows: bit 0 = key state
_CMD_SOFTWARE_PADDLE = 0x14  # 1 byte follows: bit 0 = dit, bit 1 = dah
_CMD_PTT = 0x18            # 1 byte follows: bit 0 = PTT state


class WinKeyer:
    """K1EL WinKeyer USB host-mode driver.

    Parameters
    ----------
    port:
        Serial port path (e.g. ``/dev/ttyUSB2``).
    speed:
        Initial keying speed in WPM.
    """

    def __init__(self, port: str, speed: int = 20) -> None:
        self._port_path = port
        self._speed = speed
        self._serial: serial.Serial | None = None
        self._version: int = 0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def version(self) -> int:
        return self._version

    def open(self) -> None:
        """Open the serial port and enter host mode."""
        self._serial = serial.Serial(
            self._port_path,
            baudrate=1200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=1.0,
        )
        time.sleep(0.1)
        # Drain any pending data
        self._serial.reset_input_buffer()

        # Enter host mode: admin open
        self._write(bytes([_ADMIN, _ADMIN_OPEN]))
        time.sleep(0.1)

        # Read version byte
        resp = self._serial.read(1)
        if resp:
            self._version = resp[0]
            logger.info("WinKeyer opened on %s — version %d", self._port_path, self._version)
        else:
            logger.warning("WinKeyer: no version response (may still work)")

        self.set_speed(self._speed)

    def close(self) -> None:
        """Release the key and exit host mode."""
        if not self._serial:
            return
        try:
            self.key_immediate(False)
            self._write(bytes([_ADMIN, _ADMIN_CLOSE]))
        except Exception:
            pass
        try:
            self._serial.close()
        except Exception:
            pass
        self._serial = None
        logger.info("WinKeyer closed")

    def key_immediate(self, down: bool) -> None:
        """Assert or release the KEY output immediately."""
        self._write(bytes([_CMD_KEY_IMMEDIATE, 0x01 if down else 0x00]))

    def paddle(self, dit: bool, dah: bool) -> None:
        """Set paddle state for the WinKeyer's built-in iambic keyer.

        The WinKeyer handles iambic timing internally — no host-side
        timing is needed.  This gives the lowest possible latency for
        remote paddle operation.
        """
        state = (0x01 if dit else 0x00) | (0x02 if dah else 0x00)
        self._write(bytes([_CMD_SOFTWARE_PADDLE, state]))

    def set_speed(self, wpm: int) -> None:
        """Set keying speed in WPM (5–99)."""
        wpm = max(5, min(99, wpm))
        self._speed = wpm
        self._write(bytes([_CMD_SPEED, wpm]))

    def send_text(self, text: str) -> None:
        """Queue a CW message.  WinKeyer handles spacing and timing."""
        for ch in text.upper():
            if 0x20 <= ord(ch) <= 0x7E:
                self._write(bytes([ord(ch)]))

    def clear_buffer(self) -> None:
        self._write(bytes([_CMD_CLEAR_BUFFER]))

    def _write(self, data: bytes) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.write(data)
