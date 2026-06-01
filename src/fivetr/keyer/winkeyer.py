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

Initialization sequence mirrors winkeydaemon.pl (the reference WK3 driver
used by yfktest) so the WK behaves identically under 5trig.

WinKeyer host-mode protocol reference:
  https://k1el.tripod.com/files/WK3_Datasheet_v1.3.pdf
"""

from __future__ import annotations

import logging
import threading
import time

import serial

logger = logging.getLogger(__name__)

# Admin commands  (two-byte prefix: 0x00 followed by sub-command)
_ADMIN       = 0x00
_ADMIN_OPEN  = 0x02   # enter host mode; WK replies with version byte
_ADMIN_CLOSE = 0x03   # exit host mode

# Regular commands (1 command byte + data)
_CMD_SPEED      = 0x02   # Set WPM  (1 byte: 5–99)
_CMD_WEIGHTING  = 0x03   # Set weighting  (1 byte: 10–90, 50 = neutral)
_CMD_PTT_LEAD   = 0x04   # PTT lead/tail  (2 bytes: lead_ms/10, tail_ms/10)
_CMD_PINOUT     = 0x09   # WK3 output pin config  (1 byte — see _PIN_* below)
_CMD_CLEAR      = 0x0A   # Clear transmit buffer
_CMD_KEY_IMM    = 0x0B   # Key immediate  (1 byte: 0x01=down, 0x00=up)
_CMD_WK3MODE    = 0x0E   # WK3 keyer options  (1 byte — see _WK3_* below)
_CMD_SW_PADDLE  = 0x14   # Software paddle  (1 byte: bit0=dit, bit1=dah)
_CMD_PTT        = 0x18   # PTT control  (1 byte: bit0=PTT state)

# _CMD_WK3MODE option byte (command 0x0E)
# bits [1:0]  paddle mode: 00=Iambic B  01=Iambic A  10=Ultimatic  11=Bug
# bit  2      always set (matches winkeydaemon default 0x84 / 0x04 base)
# bit  7      paddle watchdog: 1=enable 7-sec host-mode timeout
_WK3_IAMBIC_B  = 0x00
_WK3_IAMBIC_A  = 0x01
_WK3_ULTIMATIC = 0x02
_WK3_BUG       = 0x03
_WK3_BASE      = 0x04   # bit 2 always set (matches winkeydaemon behaviour)

# _CMD_PINOUT pin config byte (command 0x09)
# bit 0  PTT output enable   (1 = enabled)
# bit 1  sidetone enable     (1 = enabled)
# bits 3-2  LED / other pins (keep = 0x0C, always set in winkeydaemon)
_PIN_PTT      = 0x01
_PIN_SIDETONE = 0x02
_PIN_LED      = 0x0C


class WinKeyer:
    """K1EL WinKeyer USB host-mode driver.

    Parameters
    ----------
    port:
        Serial port path (e.g. ``/dev/ttyUSB3``).
    speed:
        Initial keying speed in WPM.
    mode:
        ``"iambic_a"`` (default), ``"iambic_b"``, or ``"straight"``.
    """

    def __init__(self, port: str, speed: int = 20, mode: str = "iambic_a") -> None:
        self._port_path = port
        self._speed = speed
        self._mode = mode
        self._serial: serial.Serial | None = None
        self._version: int = 0
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reader: threading.Thread | None = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def version(self) -> int:
        return self._version

    def open(self) -> None:
        """Open serial port and initialise WK host mode.

        Sequence mirrors winkeydaemon.pl so the WK behaves identically:
          ADMIN_CLOSE (reset prior state) → ADMIN_OPEN → WK3 mode →
          weighting → pin config → PTT lead → speed
        """
        self._serial = serial.Serial(
            self._port_path,
            baudrate=1200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=1.0,
        )
        time.sleep(0.1)

        # Reset any prior host-mode session
        self._serial.reset_input_buffer()
        self._write(bytes([_ADMIN, _ADMIN_CLOSE]))
        time.sleep(0.05)
        self._serial.reset_input_buffer()

        # Enter host mode — WK replies with a version byte
        self._write(bytes([_ADMIN, _ADMIN_OPEN]))
        time.sleep(0.3)

        resp = self._serial.read(1)
        if resp:
            self._version = resp[0]
            logger.info(
                "WinKeyer opened on %s — version %d, mode=%s, wpm=%d",
                self._port_path, self._version, self._mode, self._speed,
            )
        else:
            logger.warning("WinKeyer: no version response on %s (continuing)", self._port_path)

        # WK3 keyer mode (0x0E): iambic A/B, no watchdog timeout
        self._send_wk3mode()
        time.sleep(0.1)

        # Neutral weighting (winkeydaemon default: 0x32 = 50)
        self._write(bytes([_CMD_WEIGHTING, 0x32]))
        time.sleep(0.1)

        # Output pins: sidetone on, PTT on (winkeydaemon default: 0x0F)
        self._write(bytes([_CMD_PINOUT, _PIN_PTT | _PIN_SIDETONE | _PIN_LED]))
        time.sleep(0.1)

        # PTT lead 10 ms, tail 0 ms (winkeydaemon: 0x04 0x01 0x00)
        self._write(bytes([_CMD_PTT_LEAD, 0x01, 0x00]))
        time.sleep(0.1)

        # Speed
        self.set_speed(self._speed)

        # Background reader — drains WK status bytes so the serial receive
        # buffer doesn't stall (WK sends a status byte on every paddle event,
        # speed-pot change, message completion, etc.)
        self._stop_evt.clear()
        self._reader = threading.Thread(
            target=self._read_loop, name="wk-status", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        """Release key, exit host mode, close port."""
        self._stop_evt.set()
        if self._reader:
            self._reader.join(timeout=2)
            self._reader = None

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
        self._write(bytes([_CMD_KEY_IMM, 0x01 if down else 0x00]))

    def paddle(self, dit: bool, dah: bool) -> None:
        """Set paddle state for the WinKeyer's built-in iambic keyer.

        The WinKeyer handles iambic timing internally — no host-side
        timing is needed.  This gives the lowest possible latency for
        remote paddle operation.
        """
        state = (0x01 if dit else 0x00) | (0x02 if dah else 0x00)
        self._write(bytes([_CMD_SW_PADDLE, state]))

    def set_speed(self, wpm: int) -> None:
        """Set keying speed in WPM (5–99)."""
        wpm = max(5, min(99, wpm))
        self._speed = wpm
        self._write(bytes([_CMD_SPEED, wpm]))

    def set_mode(self, mode: str) -> None:
        """Update iambic mode and push to hardware."""
        self._mode = mode
        self._send_wk3mode()

    def send_text(self, text: str) -> None:
        """Queue a CW message.  WinKeyer handles spacing and timing."""
        for ch in text.upper():
            if 0x20 <= ord(ch) <= 0x7E:
                self._write(bytes([ord(ch)]))

    def clear_buffer(self) -> None:
        self._write(bytes([_CMD_CLEAR]))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_wk3mode(self) -> None:
        """Send WK3 keyer options byte (command 0x0E).

        Matches winkeydaemon.pl default (0x84 for iambic B, watchdog on)
        but with watchdog disabled (bit 7 = 0) so host-mode persists, and
        iambic A/B selected from config.
        """
        mode_bits = {
            "iambic_a":  _WK3_IAMBIC_A,
            "iambic_b":  _WK3_IAMBIC_B,
            "straight":  _WK3_BUG,
        }.get(self._mode, _WK3_IAMBIC_A)
        mode_byte = _WK3_BASE | mode_bits  # bit 2 always set, no watchdog
        self._write(bytes([_CMD_WK3MODE, mode_byte]))
        logger.debug("WK3 mode sent: 0x%02x (%s)", mode_byte, self._mode)

    def _read_loop(self) -> None:
        """Drain WinKeyer status bytes in a background thread."""
        assert self._serial is not None
        while not self._stop_evt.is_set():
            try:
                data = self._serial.read(1)
                if data:
                    logger.debug("WK status: 0x%02x", data[0])
            except Exception:
                break

    def _write(self, data: bytes) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.write(data)
