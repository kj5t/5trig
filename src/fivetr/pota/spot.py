"""POTA (Parks on the Air) spot data model and API parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from ..bandplan import freq_to_band


@dataclass
class PotaSpot:
    activator: str
    reference: str          # park code, e.g. US-2703
    park_name: str
    location: str            # e.g. US-GA
    freq_khz: float
    mode: str
    spotter: str
    comment: str
    spot_time: str           # ISO 8601 UTC, as reported by the API
    band: str = ""
    received_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.received_at is None:
            self.received_at = datetime.now(tz=timezone.utc)
        if not self.band:
            self.band = freq_to_band(self.freq_khz)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Optional["PotaSpot"]:
        reference = d.get("reference")
        activator = d.get("activator")
        if not reference or not activator:
            return None
        try:
            freq_khz = float(d["frequency"])
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            activator=activator,
            reference=reference,
            park_name=d.get("name") or d.get("parkName") or "",
            location=d.get("locationDesc") or "",
            freq_khz=freq_khz,
            mode=(d.get("mode") or "").upper(),
            spotter=d.get("spotter") or "",
            comment=d.get("comments") or "",
            spot_time=d.get("spotTime") or "",
        )

    @property
    def freq_mhz(self) -> float:
        return self.freq_khz / 1000.0

    @property
    def time_hhmm(self) -> str:
        """HH:MM extracted from the ISO spot_time, or '' if unparseable."""
        if "T" not in self.spot_time:
            return ""
        return self.spot_time.split("T", 1)[1][:5]
