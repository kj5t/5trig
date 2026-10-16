"""
Transmit-audio path for remote operation (laptop -> shack).

TxAudioSender    — remote-side: records a PipeWire sink (the one WSJT-X plays
into) with ``pw-record`` and sends it to the shack as UDP datagrams, but only
while PTT is requested (plus a short tail so the end of the over isn't cut).

TxAudioReceiver  — shack-side: receives those datagrams and plays them into the
radio's USB sound-card sink with ``pw-play``.  Audio is accepted only from IP
addresses of currently connected 5trig clients, and is passed to the radio only
while PTT is on (plus the same short tail).  Everything else is dropped.

Wire format: 4-byte big-endian sequence number, then raw signed 16-bit
little-endian mono PCM at the stream sample rate.  Raw PCM rather than Opus:
digital modes dislike codec artefacts and a LAN has bandwidth to spare
(48 kHz x 16 bit = 96 kB/s, only while transmitting).

This is *not* authentication.  The IP check plus the PTT gate keep stray
packets off the air, but the port should still never be exposed to the internet.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Callable

import numpy as np

from .audio import pipewire_sink_exists

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 960                  # 20 ms at 48 kHz
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_PAYLOAD = 4096                   # sanity bound for a received datagram
HOLD_SECONDS = 0.5                   # keep passing audio this long after PTT drops
PREBUFFER_FRAMES = 4                 # 80 ms queued before an over starts playing
OVER_GAP_SECONDS = 0.25              # silence this long = the next packet starts a new over

_SEQ = struct.Struct(">I")


def _peak(pcm: bytes) -> int:
    """Largest absolute sample in a block of s16le PCM (0 for an empty block)."""
    if len(pcm) < 2:
        return 0
    return int(np.abs(np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.int32)).max())


class TxGate:
    """True while *active_fn* is true, and for *hold* seconds afterwards."""

    def __init__(
        self,
        active_fn: Callable[[], bool],
        hold: float = HOLD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._active = active_fn
        self._hold = hold
        self._clock = clock
        self._last_active = -1e9

    def is_open(self) -> bool:
        now = self._clock()
        if self._active():
            self._last_active = now
            return True
        return now - self._last_active < self._hold


class TxAudioSender:
    """Capture a PipeWire sink and send it to the shack while transmitting."""

    def __init__(
        self,
        server_host: str,
        server_udp_port: int,
        capture_sink: str,
        gate: TxGate,
        sample_rate: int = 48000,
    ) -> None:
        self._server = (server_host, server_udp_port)
        self._sink = capture_sink
        self._gate = gate
        self._sample_rate = sample_rate
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._seq = 0
        self.packets_sent = 0
        self._over_packets = 0
        self._over_peak = 0

    def start(self) -> None:
        self.error = None
        if not pipewire_sink_exists(self._sink):
            self.error = (
                f"TX audio sink '{self._sink}' not found - "
                "run scripts/wsjtx-audio.sh up, or fix the name in Settings > Remote"
            )
            logger.error("%s; transmit audio disabled", self.error)
            return
        try:
            self._proc = subprocess.Popen(
                [
                    "pw-record", "-P", "stream.capture.sink=true",
                    "--target", self._sink,
                    "--rate", str(self._sample_rate), "--channels", "1",
                    "--format", "s16", "--latency", "20ms", "-a", "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.error = f"TX audio: could not start pw-record ({exc})"
            logger.error("Could not start pw-record: %s", exc)
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="tx-audio-sender", daemon=True
        )
        self._thread.start()
        logger.info("TX audio: %s -> %s:%d", self._sink, *self._server)

    def stop(self) -> None:
        self._running = False
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None

    def _run(self) -> None:
        proc, sock = self._proc, self._sock
        assert proc is not None and proc.stdout is not None and sock is not None
        while self._running:
            data = proc.stdout.read(FRAME_BYTES)
            if len(data) < FRAME_BYTES:
                break                      # pw-record exited or we were stopped
            if not self._gate.is_open():
                if self._over_packets:
                    logger.info(
                        "TX audio over finished: sent %d packets (%.1f s), peak level %d/32767",
                        self._over_packets, self._over_packets * FRAME_SAMPLES / self._sample_rate,
                        self._over_peak,
                    )
                    if self._over_peak == 0:
                        logger.warning(
                            "TX audio was silent: nothing is playing into '%s' "
                            "(check WSJT-X Settings > Audio > Output)", self._sink,
                        )
                    self._over_packets = 0
                    self._over_peak = 0
                continue
            if self._over_packets == 0:
                logger.info("TX audio over started: PTT requested, sending to %s:%d", *self._server)
            self._over_peak = max(self._over_peak, _peak(data))
            packet = _SEQ.pack(self._seq) + data
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            try:
                sock.sendto(packet, self._server)
                self.packets_sent += 1
                self._over_packets += 1
            except OSError as exc:
                logger.warning("TX audio send failed: %s", exc)
        if self._running:
            logger.error("pw-record exited; transmit audio stopped")


class TxAudioReceiver:
    """Receive TX audio from connected clients and play it into the radio."""

    def __init__(
        self,
        host: str,
        port: int,
        sink: str,
        allowed_ips: Callable[[], set[str]],
        gate: TxGate,
        sample_rate: int = 48000,
    ) -> None:
        self._host = host
        self._port = port
        self._sink = sink
        self._allowed_ips = allowed_ips
        self._gate = gate
        self._sample_rate = sample_rate
        self.error: str | None = None
        self._sock: socket.socket | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_seq: int | None = None
        # pw-play pulls from its stdin with no buffering of its own, so a packet that
        # arrives a little late plays as a gap.  Hold back PREBUFFER_FRAMES at the start
        # of each over so the pipe stays ahead of the player.
        self._prebuf: list[bytes] = []
        self._primed = False
        self._last_pcm = -1e9
        self._over_played = 0
        self._over_peak = 0
        self._last_warn: dict[str, float] = {}
        # counters, mostly for tests and debugging
        self.played = 0
        self.dropped_foreign = 0
        self.dropped_ungated = 0
        self.dropped_bad = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self.error = None
        if not pipewire_sink_exists(self._sink):
            self.error = f"TX audio sink '{self._sink}' not found; remote transmit audio disabled"
            logger.error(self.error)
            return
        try:
            self._proc = subprocess.Popen(
                [
                    "pw-play", "--target", self._sink,
                    "--rate", str(self._sample_rate), "--channels", "1",
                    "--format", "s16", "--latency", "40ms", "-a", "-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.error = f"TX audio: could not start pw-play ({exc})"
            logger.error("Could not start pw-play: %s", exc)
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        if self._port == 0:
            self._port = self._sock.getsockname()[1]
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="tx-audio-receiver", daemon=True
        )
        self._thread.start()
        logger.info("TX audio receiver on %s:%d -> %s", self._host, self._port, self._sink)

    def stop(self) -> None:
        self._running = False
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
        proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    # ------------------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            sock, proc = self._sock, self._proc
            if sock is None or proc is None or proc.stdin is None:
                break
            try:
                data, addr = sock.recvfrom(MAX_PAYLOAD + _SEQ.size)
            except TimeoutError:
                self._finish_over()
                continue
            except OSError:
                break
            pcm = self.accept(data, addr[0])
            if pcm is None:
                continue
            try:
                if self._over_played == 0:
                    logger.info("TX audio over started from %s", addr[0])
                self._over_played += 1
                self._over_peak = max(self._over_peak, _peak(pcm))
                for chunk in self._prime(pcm):
                    proc.stdin.write(chunk)
                    proc.stdin.flush()
                    self.played += 1
            except (BrokenPipeError, ValueError, OSError):
                logger.error("pw-play exited; remote transmit audio stopped")
                break

    def _finish_over(self) -> None:
        if self._over_played and time.monotonic() - self._last_pcm > OVER_GAP_SECONDS:
            logger.info(
                "TX audio over finished: played %d packets (%.1f s) into '%s', peak level %d/32767",
                self._over_played, self._over_played * FRAME_SAMPLES / self._sample_rate,
                self._sink, self._over_peak,
            )
            self._over_played = 0
            self._over_peak = 0

    def _warn(self, key: str, msg: str, *args) -> None:
        """Warn at most once every 5 s per reason, so a stream can't flood the log."""
        now = time.monotonic()
        if now - self._last_warn.get(key, -1e9) >= 5.0:
            self._last_warn[key] = now
            logger.warning(msg, *args)

    def _prime(self, pcm: bytes, now: float | None = None) -> list[bytes]:
        """Chunks to write now: nothing while filling the prebuffer, then everything."""
        now = time.monotonic() if now is None else now
        if now - self._last_pcm > OVER_GAP_SECONDS:
            self._primed = False
            self._prebuf = []
        self._last_pcm = now
        if self._primed:
            return [pcm]
        self._prebuf.append(pcm)
        if len(self._prebuf) < PREBUFFER_FRAMES:
            return []
        self._primed = True
        out, self._prebuf = self._prebuf, []
        return out

    def accept(self, data: bytes, ip: str) -> bytes | None:
        """Return the PCM to play for one datagram, or None to drop it."""
        if ip not in self._allowed_ips():
            self.dropped_foreign += 1
            self._warn("foreign", "TX audio from %s dropped: not a connected 5trig client "
                       "(connected: %s)", ip, sorted(self._allowed_ips()) or "none")
            return None
        payload = data[_SEQ.size:]
        if len(data) <= _SEQ.size or len(payload) % 2 or len(payload) > MAX_PAYLOAD:
            self.dropped_bad += 1
            return None
        (seq,) = _SEQ.unpack_from(data)
        if self._last_seq is not None:
            ahead = (seq - self._last_seq) & 0xFFFFFFFF
            if ahead == 0 or ahead >= 0x80000000:
                self.dropped_bad += 1        # duplicate or late datagram
                return None
        self._last_seq = seq
        if not self._gate.is_open():
            self.dropped_ungated += 1
            self._warn("ungated", "TX audio from %s dropped: the radio is not keyed "
                       "(no PTT request seen from the client)", ip)
            return None
        return payload
