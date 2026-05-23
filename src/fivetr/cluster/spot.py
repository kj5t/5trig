"""DX spot data model and parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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


def _freq_to_band(freq_khz: float) -> str:
    mhz = freq_khz / 1000.0
    ranges = [
        (1.8, 2.0, "160m"),
        (3.5, 4.0, "80m"),
        (5.0, 5.5, "60m"),
        (7.0, 7.3, "40m"),
        (10.1, 10.15, "30m"),
        (14.0, 14.35, "20m"),
        (18.068, 18.168, "17m"),
        (21.0, 21.45, "15m"),
        (24.89, 24.99, "12m"),
        (28.0, 29.7, "10m"),
        (50.0, 54.0, "6m"),
        (144.0, 148.0, "2m"),
        (430.0, 440.0, "70cm"),
    ]
    for lo, hi, band in ranges:
        if lo <= mhz <= hi:
            return band
    return "?"


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
