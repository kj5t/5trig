"""
Virtual CAT TCP server.

Listens on a configurable TCP port and speaks the Kenwood TS-2000 CAT dialect
(the de-facto ham software standard supported by WSJT-X, JS8Call, fldigi,
Log4OM, etc.).

Commands handled:
  FA  — VFO-A frequency get/set
  FB  — VFO-B frequency get/set
  MD  — mode get/set
  TX  — PTT get/set
  IF  — composite status (TS-2000 IF; command)
  ID  — radio model identifier → "ID019;" (TS-2000)
  AI  — auto-information (ignored, always returns AI0;)
  PS  — power status → "PS1;"
  RX / TX — receive / transmit

Each connected client gets its own asyncio StreamReader/StreamWriter pair so
multiple programs can query simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..cat.base import CATRadio, Mode

logger = logging.getLogger(__name__)

# TS-2000 mode codes (not the same as Yaesu codes)
_TS2000_MODE_MAP: dict[str, str] = {
    "1": "2",   # LSB → TS2000 LSB
    "2": "3",   # USB → TS2000 USB
    "3": "7",   # CW  → TS2000 CW
    "4": "6",   # FM  → TS2000 FM
    "5": "5",   # AM  → TS2000 AM
    "6": "9",   # RTTY LSB → TS2000 RTTY
    "7": "8",   # CW-R
    "8": "9",   # DATA LSB → RTTY as closest
    "9": "9",   # RTTY USB
    "A": "4",   # DATA FM
    "B": "6",   # FM-N
    "C": "9",   # DATA USB
    "D": "5",   # AM-N
    "E": "6",   # C4FM
}


class VCATServer:
    """Manages a TCP server providing TS-2000 virtual CAT to external software."""

    def __init__(self, radio: "CATRadio", host: str = "127.0.0.1", port: int = 4532) -> None:
        self._radio = radio
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            reuse_address=True,
        )
        logger.info("Virtual CAT server listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        logger.info("vCAT client connected: %s", addr)
        self._clients.add(writer)

        try:
            while True:
                data = await reader.read(256)
                if not data:
                    break
                for cmd in self._split_commands(data.decode(errors="ignore")):
                    reply = self._process(cmd)
                    if reply:
                        writer.write(reply.encode())
                        await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            logger.info("vCAT client disconnected: %s", addr)

    # ------------------------------------------------------------------
    # Command processing
    # ------------------------------------------------------------------

    @staticmethod
    def _split_commands(text: str) -> list[str]:
        """Split input on ';' to get individual CAT tokens."""
        return [t.strip() for t in text.split(";") if t.strip()]

    def _process(self, cmd: str) -> str | None:
        state = self._radio.state
        cmd_upper = cmd.upper()

        # --- Frequency ---
        if cmd_upper.startswith("FA") and len(cmd_upper) > 2:
            hz = int(cmd_upper[2:])
            self._radio.set_freq(hz, vfo=None)   # type: ignore[arg-type]
            return None
        if cmd_upper == "FA":
            return f"FA{state.vfo_a_hz:011d};"

        if cmd_upper.startswith("FB") and len(cmd_upper) > 2:
            hz = int(cmd_upper[2:])
            self._radio.set_freq(hz, vfo=None)   # type: ignore[arg-type]
            return None
        if cmd_upper == "FB":
            return f"FB{state.vfo_b_hz:011d};"

        # --- Mode ---
        if cmd_upper.startswith("MD") and len(cmd_upper) > 2:
            from ..cat.base import Mode
            yaesu_code = self._ts2000_mode_to_yaesu(cmd_upper[2:])
            self._radio.set_mode(Mode.from_code(yaesu_code))
            return None
        if cmd_upper == "MD":
            ts_mode = _TS2000_MODE_MAP.get(state.mode.value, "3")
            return f"MD{ts_mode};"

        # --- PTT ---
        if cmd_upper == "TX":
            return "TX1;" if state.ptt else "TX0;"
        if cmd_upper == "TX1":
            self._radio.set_ptt(True)
            return None
        if cmd_upper in ("TX0", "RX"):
            self._radio.set_ptt(False)
            return None

        # --- Composite IF status (TS-2000 format) ---
        if cmd_upper == "IF":
            return self._build_if_reply()

        # --- Identity ---
        if cmd_upper == "ID":
            return "ID019;"   # TS-2000 ID

        # --- Power ---
        if cmd_upper == "PS":
            return "PS1;"

        # --- Auto-info (disable) ---
        if cmd_upper.startswith("AI"):
            return "AI0;"

        # --- Unrecognized ---
        logger.debug("vCAT: unhandled command: %r", cmd)
        return "?;"

    def _build_if_reply(self) -> str:
        """TS-2000 IF; reply — 37 bytes of packed state."""
        s = self._radio.state
        hz = s.vfo_a_hz
        rit_hz = s.rit_hz if s.rit_on else 0
        rit_sign = "+" if rit_hz >= 0 else "-"
        rit_abs = abs(rit_hz)
        mode = _TS2000_MODE_MAP.get(s.mode.value, "3")
        ptt = "1" if s.ptt else "0"
        split = "1" if s.split else "0"
        # Format: IF<11-digit freq><5-char rit hz><rit sign><rit on><xit on>
        #         <mem bank><mem ch><rx/tx><mode><vfo><scan><split><tone><ctcss tone>
        return (
            f"IF{hz:011d}"
            f"{rit_abs:04d}{rit_sign}"
            f"{'1' if s.rit_on else '0'}"
            f"{'1' if s.xit_on else '0'}"
            f"00000"     # mem bank + ch
            f"{ptt}"
            f"{mode}"
            f"0"         # VFO-A active = 0
            f"0"         # scan off
            f"{split}"
            f"00"        # no tone
            f";"
        )

    @staticmethod
    def _ts2000_mode_to_yaesu(ts_code: str) -> str:
        reverse = {v: k for k, v in _TS2000_MODE_MAP.items()}
        return reverse.get(ts_code, "2")  # default USB
