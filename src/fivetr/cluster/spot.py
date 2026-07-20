"""DX spot data model and parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..bandplan import freq_to_band as _freq_to_band

# DX Spider spot format:
# DX de W6ABC:      14074.0  VK2XYZ       FT8 -12 dB            1234Z
_SPOT_RE = re.compile(
    r"DX\s+de\s+(\S+?):\s+"       # spotter callsign
    r"([\d.]+)\s+"                  # frequency kHz
    r"(\S+)\s+"                     # DX callsign
    r"(.*?)\s+"                     # comment
    r"(\d{4})Z",                    # time HHMM UTC
    re.IGNORECASE,
)


@dataclass
class DXSpot:
    spotter: str
    dx_call: str
    freq_khz: float
    comment: str
    time_utc: str       # HHMM
    band: str = ""
    mode: str = ""
    received_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.received_at is None:
            self.received_at = datetime.now(tz=timezone.utc)
        if not self.band:
            self.band = _freq_to_band(self.freq_khz)
        if not self.mode:
            self.mode = _guess_mode(self.comment)

    @classmethod
    def parse(cls, line: str) -> Optional["DXSpot"]:
        m = _SPOT_RE.search(line)
        if not m:
            return None
        return cls(
            spotter=m.group(1).rstrip("-"),
            freq_khz=float(m.group(2)),
            dx_call=m.group(3),
            comment=m.group(4).strip(),
            time_utc=m.group(5),
        )

    @property
    def freq_mhz(self) -> float:
        return self.freq_khz / 1000.0


def _guess_mode(comment: str) -> str:
    c = comment.upper()
    if "FT8" in c:
        return "FT8"
    if "FT4" in c:
        return "FT4"
    if "WSPR" in c:
        return "WSPR"
    if "RTTY" in c:
        return "RTTY"
    if "PSK" in c:
        return "PSK"
    if "CW" in c:
        return "CW"
    if "SSB" in c or "USB" in c or "LSB" in c:
        return "SSB"
    return ""
