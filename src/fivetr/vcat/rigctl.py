"""
Hamlib rigctld ("NET rigctl") protocol handler for the virtual CAT server.

WSJT-X, JS8Call, fldigi and anything else built on Hamlib can talk to a radio
over TCP by choosing the "Hamlib NET rigctl" rig.  They speak the rigctld text
protocol: one command per line, ``\\long_name`` or a short letter, replies are
plain lines or ``RPRT <code>``.

RigctlHandler is transport-free: feed it one line, get the reply text (or None
for "close the connection").  VCATServer owns the sockets.

Replies were modelled on a real ``rigctld -m 1`` (Hamlib 4.7.2) so that the
stock ``rigctl -m 2`` client accepts them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..cat.base import VFO, Mode

if TYPE_CHECKING:
    from ..cat.base import CATRadio

logger = logging.getLogger(__name__)

RPRT_OK = "RPRT 0\n"
RPRT_EINVAL = "RPRT -1\n"
RPRT_ENIMPL = "RPRT -11\n"

# 5trig Mode -> (Hamlib mode name, nominal passband Hz)
_MODE_TO_HAMLIB: dict[Mode, tuple[str, int]] = {
    Mode.LSB: ("LSB", 2400),
    Mode.USB: ("USB", 2400),
    Mode.CW: ("CW", 500),
    Mode.CW_R: ("CWR", 500),
    Mode.FM: ("FM", 15000),
    Mode.FM_N: ("FM", 9000),
    Mode.AM: ("AM", 6000),
    Mode.AM_N: ("AM", 3000),
    Mode.RTTY_LSB: ("RTTY", 500),
    Mode.RTTY_USB: ("RTTYR", 500),
    Mode.DATA_LSB: ("PKTLSB", 3000),
    Mode.DATA_USB: ("PKTUSB", 3000),
    Mode.DATA_FM: ("PKTFM", 15000),
    Mode.C4FM: ("FM", 15000),
}

# Hamlib mode name -> 5trig Mode (for "M <mode> <passband>")
_HAMLIB_TO_MODE: dict[str, Mode] = {
    "LSB": Mode.LSB,
    "USB": Mode.USB,
    "CW": Mode.CW,
    "CWR": Mode.CW_R,
    "FM": Mode.FM,
    "AM": Mode.AM,
    "RTTY": Mode.RTTY_LSB,
    "RTTYR": Mode.RTTY_USB,
    "PKTLSB": Mode.DATA_LSB,
    "PKTUSB": Mode.DATA_USB,
    "PKTFM": Mode.DATA_FM,
}

# Long command names -> the short letter they stand for.
_LONG_TO_SHORT = {
    "get_freq": "f",
    "set_freq": "F",
    "get_mode": "m",
    "set_mode": "M",
    "get_ptt": "t",
    "set_ptt": "T",
    "get_vfo": "v",
    "set_vfo": "V",
    "get_split_vfo": "s",
    "set_split_vfo": "S",
    "get_rit": "j",
    "get_xit": "z",
    "quit": "q",
}


# Routine polling commands; not worth a log line each time.
_QUIET = {"f", "m", "t", "v", "s", "j", "z", "\\get_freq", "\\get_mode", "\\get_ptt",
          "\\get_vfo", "\\chk_vfo", "\\get_powerstat", "\\get_lock_mode"}


def _dump_state(model: str) -> str:
    """Capability block that Hamlib's netrigctl client reads right after connecting."""
    # protocol version, rig model, ITU region
    lines = ["1", "2", "0"]
    # rx ranges: start end modes low_power high_power vfo ant
    for lo, hi in ((1_800_000, 54_000_000),):
        lines.append(f"{lo}.000000 {hi}.000000 0x1ff -1 -1 0x17e00007 0x1f")
    lines.append("0 0 0 0 0 0 0")
    # tx ranges
    for lo, hi in ((1_800_000, 54_000_000),):
        lines.append(f"{lo}.000000 {hi}.000000 0x1ff 5000 100000 0x17e00007 0x1f")
    lines.append("0 0 0 0 0 0 0")
    # tuning steps (mode mask, step Hz) then terminator
    lines += ["0x1ff 1", "0x1ff 0", "0 0"]
    # filters (mode mask, width Hz) then terminator
    lines += [
        "0xc 2400", "0xc 1800", "0xc 3000", "0xc 0",
        "0x2 500", "0x2 2400", "0x2 50", "0x2 0",
        "0x10 300", "0x10 2400", "0x10 50", "0x10 0",
        "0x1 8000", "0x1 2400", "0x1 10000",
        "0x20 15000", "0x20 8000", "0x40 230000",
        "0 0",
    ]
    # max RIT, max XIT, max IF shift, announces
    lines += ["9990", "9990", "10000", "0"]
    # preamp list, attenuator list
    lines += ["10 ", "10 20 30 "]
    # has_get_func, has_set_func, has_get_level, has_set_level, has_get_parm, has_set_parm
    lines += [
        "0xffffffffffffffff",
        "0xffffffffffffffff",
        "0xfffffffff7ffffff",
        "0xfffeff7083ffffff",
        "0xffffffffffffffff",
        "0xffffffffffffffbf",
    ]
    lines += [
        "vfo_ops=0x0",
        "ptt_type=0x1",
        "targetable_vfo=0x0",
        "has_set_vfo=1",
        "has_get_vfo=1",
        "has_set_freq=1",
        "has_get_freq=1",
        "timeout=0",
        "rig_model=2",
        f"rigctld_version=5trig vCAT ({model})",
        "done",
    ]
    return "\n".join(lines) + "\n"


class RigctlHandler:
    """Translate rigctld commands into calls on a CATRadio."""

    def __init__(self, radio: CATRadio) -> None:
        self._radio = radio
        self.closing = False    # set after "q": send the reply, then drop the connection

    def process(self, line: str) -> str:
        """Handle one command line and return the reply text ('' for no reply)."""
        line = line.strip()
        if not line:
            return ""
        # Extended-response prefixes ('+', ';', '|') are not supported; plain protocol only.
        if line[0] in "+;|":
            line = line[1:].lstrip()

        # Log everything except the reads that WSJT-X polls several times a second.
        if line not in _QUIET:
            logger.info("rigctl <- %s", line)

        if line.startswith("\\"):
            name, _, args = line[1:].partition(" ")
            if name == "chk_vfo":
                return "0\n"
            if name == "dump_state":
                return _dump_state(self._radio.state.model)
            if name == "get_powerstat":
                return "1\n"
            if name == "get_info":
                return f"5trig vCAT ({self._radio.state.model})\n"
            if name in ("set_powerstat", "set_lock_mode"):
                return RPRT_OK
            if name == "get_lock_mode":
                return "0\nRPRT 0\n"   # Hamlib's set_mode asks this first and waits for both lines
            letter = _LONG_TO_SHORT.get(name)
            if letter is None:
                logger.warning("rigctl: unsupported command %r", line)
                return RPRT_ENIMPL
            return self._short(letter, args.strip())

        letter, _, args = line.partition(" ")
        return self._short(letter, args.strip())

    # ------------------------------------------------------------------

    def _short(self, letter: str, args: str) -> str:
        s = self._radio.state

        if letter == "f":
            hz = s.vfo_b_hz if s.active_vfo is VFO.B else s.vfo_a_hz
            return f"{hz}\n"
        if letter == "F":
            try:
                hz = int(float(args.split()[-1]))
            except (ValueError, IndexError):
                return RPRT_EINVAL
            self._radio.set_freq(hz, vfo=s.active_vfo)
            return RPRT_OK

        if letter == "m":
            name, width = _MODE_TO_HAMLIB.get(s.mode, ("USB", 2400))
            return f"{name}\n{width}\n"
        if letter == "M":
            parts = args.split()
            if not parts:
                return RPRT_EINVAL
            mode = _HAMLIB_TO_MODE.get(parts[-2].upper() if len(parts) > 1 else parts[0].upper())
            if mode is None:
                return RPRT_EINVAL
            self._radio.set_mode(mode)
            return RPRT_OK

        if letter == "t":
            return "1\n" if s.ptt else "0\n"
        if letter == "T":
            try:
                value = int(args.split()[-1])
            except (ValueError, IndexError):
                return RPRT_EINVAL
            self._radio.set_ptt(value != 0)
            return RPRT_OK

        if letter == "v":
            return "VFOB\n" if s.active_vfo is VFO.B else "VFOA\n"
        if letter == "V":
            return RPRT_OK    # VFO selection is owned by the 5trig UI

        if letter == "s":
            return "1\nVFOB\n" if s.split else "0\nVFOA\n"
        if letter == "S":
            logger.warning("rigctl: split is not supported (S %s)", args)
            return RPRT_ENIMPL

        if letter == "j":
            return f"{s.rit_hz if s.rit_on else 0}\n"
        if letter == "z":
            return f"{s.xit_hz if s.xit_on else 0}\n"

        if letter in ("q", "Q"):
            self.closing = True
            return RPRT_OK

        logger.warning("rigctl: unsupported command %r %r", letter, args)
        return RPRT_ENIMPL
