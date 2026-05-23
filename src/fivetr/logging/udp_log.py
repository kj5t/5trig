"""
UDP log broadcaster.

Sends QSO data as UDP datagrams in formats understood by:
  - N1MM+ Logger (port 2333)
  - WSJT-X / JTDX (port 2333, XML format)
  - Log4OM (port 2237)
  - Any custom listener

The N1MM+ format is XML; WSJT-X uses its own XML dialect.
We implement both, selectable per destination.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .adif import QSORecord

logger = logging.getLogger(__name__)

LogFormat = Literal["n1mm", "wsjtx", "adif_text"]


@dataclass
class UDPDestination:
    host: str
    port: int
    fmt: LogFormat = "n1mm"


class UDPLogger:
    """Broadcasts QSO records to one or more UDP destinations."""

    def __init__(self) -> None:
        self._destinations: list[UDPDestination] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def add_destination(self, dest: UDPDestination) -> None:
        self._destinations.append(dest)

    def remove_destination(self, dest: UDPDestination) -> None:
        self._destinations.remove(dest)

    def log_qso(self, qso: QSORecord) -> None:
        """Send QSO to all registered destinations (sync, fire-and-forget)."""
        for dest in self._destinations:
            try:
                payload = self._format(qso, dest.fmt)
                self._sock.sendto(payload, (dest.host, dest.port))
                logger.debug("UDP log → %s:%d (%s)", dest.host, dest.port, dest.fmt)
            except OSError as e:
                logger.warning("UDP log error: %s", e)

    async def log_qso_async(self, qso: QSORecord) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.log_qso, qso)

    def close(self) -> None:
        self._sock.close()

    # ------------------------------------------------------------------
    # Formatters
    # ------------------------------------------------------------------

    def _format(self, qso: QSORecord, fmt: LogFormat) -> bytes:
        if fmt == "n1mm":
            return self._n1mm_xml(qso).encode("utf-8")
        if fmt == "wsjtx":
            return self._wsjtx_xml(qso).encode("utf-8")
        if fmt == "adif_text":
            return self._adif_oneline(qso).encode("utf-8")
        return b""

    @staticmethod
    def _n1mm_xml(qso: QSORecord) -> str:
        """N1MM+ ContactInfo XML format."""
        root = ET.Element("contactinfo")
        fields = {
            "call": qso.call,
            "freq": str(qso.freq_khz / 1000.0),  # MHz
            "band": qso.band,
            "mode": qso.mode,
            "date": qso.qso_date,
            "time": qso.time_on,
            "rst_sent": qso.rst_sent,
            "rst_rcvd": qso.rst_rcvd,
            "name": qso.name,
            "qth": qso.qth,
        }
        for tag, val in fields.items():
            el = ET.SubElement(root, tag)
            el.text = val
        return ET.tostring(root, encoding="unicode", xml_declaration=False)

    @staticmethod
    def _wsjtx_xml(qso: QSORecord) -> str:
        """WSJT-X style logged QSO."""
        root = ET.Element("logged_adif")
        adif = ET.SubElement(root, "ADIF")
        adif.text = (
            f"<CALL:{len(qso.call)}>{qso.call} "
            f"<FREQ:{len(str(qso.freq_khz/1000))}>{qso.freq_khz/1000:.6f} "
            f"<MODE:{len(qso.mode)}>{qso.mode} "
            f"<QSO_DATE:8>{qso.qso_date} "
            f"<TIME_ON:6>{qso.time_on} "
            f"<EOR>"
        )
        return ET.tostring(root, encoding="unicode")

    @staticmethod
    def _adif_oneline(qso: QSORecord) -> str:
        """Single-line ADIF suitable for simple listeners."""
        parts = [
            f"<CALL:{len(qso.call)}>{qso.call}",
            f"<FREQ:{len(str(qso.freq_khz))}>{qso.freq_khz}",
            f"<MODE:{len(qso.mode)}>{qso.mode}",
            f"<QSO_DATE:8>{qso.qso_date}",
            f"<TIME_ON:6>{qso.time_on}",
            "<EOR>",
        ]
        return " ".join(parts)
