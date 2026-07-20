"""Amateur radio band lookup from frequency."""

from __future__ import annotations

_BAND_RANGES = [
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


def freq_to_band(freq_khz: float) -> str:
    mhz = freq_khz / 1000.0
    for lo, hi, band in _BAND_RANGES:
        if lo <= mhz <= hi:
            return band
    return "?"
