"""
Receive-filter helpers shared by the CAT drivers, the remote link and the UI.

The SH (WIDTH) CAT command takes an *index* into a bandwidth table, and the table
depends on the operating mode.  Index 0 is "the radio's default for this mode".
Tables are from the FTdx-10 CAT Operation Reference Manual, Table 3.
"""

from __future__ import annotations

from .base import Mode

_SSB_WIDTHS = [
    300, 400, 600, 850, 1100, 1200, 1500, 1650, 1800, 1950, 2100, 2250,
    2400, 2450, 2500, 2600, 2700, 2800, 2900, 3000, 3200, 3500, 4000,
]
_NARROW_WIDTHS = [   # CW, RTTY and PSK/data share one table
    50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 800,
    1200, 1400, 1700, 2000, 2400, 3000,
]


IF_SHIFT_MAX = 1200   # Hz either way
IF_SHIFT_STEP = 20


def widths_for_mode(mode: Mode) -> list[int]:
    """Bandwidths in Hz for modes where SH applies; entry *n* is SH index *n+1*.

    Empty for modes the radio's width table doesn't cover (FM, AM, ...).
    """
    if mode in (Mode.LSB, Mode.USB):
        return _SSB_WIDTHS
    if mode in (Mode.CW, Mode.CW_R, Mode.RTTY_LSB, Mode.RTTY_USB,
                Mode.DATA_LSB, Mode.DATA_USB):
        return _NARROW_WIDTHS
    return []


def width_label(mode: Mode, idx: int) -> str:
    """'500 Hz' for SH index *idx* in *mode*; 'Default' for index 0."""
    table = widths_for_mode(mode)
    if idx == 0:
        return "Default"
    if 1 <= idx <= len(table):
        return f"{table[idx - 1]} Hz"
    return f"#{idx}"
