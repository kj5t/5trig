"""
Opus-over-UDP audio streaming for 5trig remote operation.

AudioUDPServer  — shack-side: accepts PCM from AudioScopeSource, encodes
with Opus, and sends UDP packets to all registered remote clients.

AudioUDPClient  — remote-side: receives UDP packets, decodes Opus, plays
through the local sound output via sounddevice OutputStream with a jitter
buffer.

UDP packet format:
  Bytes 0-3  sequence number (uint32 big-endian) — used for loss detection
  Bytes 4-5  payload length  (uint16 big-endian)
  Bytes 6+   Opus-encoded frame

Client registration:
  Client sends b"AUDIO_HELLO" to the server UDP port to register.
  Client sends b"AUDIO_BYE"   to deregister on clean disconnect.
  Server silently drops a client after the first failed sendto().

The server's UDP port is advertised in the TCP hello message
(key "audio_udp_port") so that no additional out-of-band configuration
is needed.  If the client receives no such key it skips audio setup.
"""

from __future__ import annotations

import collections
import logging
import socket
import struct
import threading

import numpy as np

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 960           # 20 ms at 48 kHz — standard Opus frame size
DEFAULT_BITRATE = 64_000      # 64 kbps — excellent for SSB / CW
MAX_BUFFER_FRAMES = 15        # ~300 ms jitter buffer on the receiver

_HDR = struct.Struct(">IH")   # seq (uint32) + payload_len (uint16)


class AudioUDPServer:
    """Shack-side Opus/UDP audio broadcaster.

    Parameters
    ----------
    host:
        UDP bind address. ``"0.0.0.0"`` accepts connections on all
        interfaces; ``"127.0.0.1"`` restricts to loopback.
    port:
        UDP port to bind.  Default 5041 (separate from the TCP control
        port 5040 so each can be firewalled independently).
    sample_rate:
        Must match the AudioScopeSource sample rate (typically 48000).
    bitrate:
        Opus encoder target bitrate in bps.  64 000 is recommended for
        SSB voice; 32 000 is acceptable for CW-only operation.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5041,
        sample_rate: int = 48000,
        bitrate: int = DEFAULT_BITRATE,
    ) -> None:
        self._host = host
        self._port = port
        self._sample_rate = sample_rate
        self._bitrate = bitrate

        self._encoder = None
        self._sock: socket.socket | None = None
        self._clients: set[tuple] = set()
        self._clients_lock = threading.Lock()
        self._seq = 0

        # PCM accumulator — collect incoming samples until we have a full
        # Opus frame (FRAME_SAMPLES) worth.
        self._pcm_buf = np.zeros(0, dtype=np.float32)
        self._pcm_lock = threading.Lock()

        self._recv_thread: threading.Thread | None = None
        self._running = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    def start(self) -> None:
        """Bind the UDP socket and start the registration listener."""
        try:
            import opuslib
        except ImportError:
            logger.error("opuslib not installed; audio UDP server unavailable")
            return

        self._encoder = opuslib.Encoder(
            self._sample_rate, 1, opuslib.APPLICATION_AUDIO
        )
        self._encoder.bitrate = self._bitrate

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.settimeout(1.0)

        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="audio-udp-server", daemon=True
        )
        self._recv_thread.start()
        logger.info("Audio UDP server listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._recv_thread:
            self._recv_thread.join(timeout=2)
            self._recv_thread = None
        with self._clients_lock:
            self._clients.clear()
        logger.info("Audio UDP server stopped")

    def on_pcm(self, samples: np.ndarray) -> None:
        """Feed raw float32 mono PCM samples.  Called from the audio thread.

        Accumulates samples until a full Opus frame is available, then
        encodes and sends to all registered clients.
        """
        with self._clients_lock:
            has_clients = bool(self._clients)
        if not has_clients or self._encoder is None or self._sock is None:
            return

        with self._pcm_lock:
            self._pcm_buf = np.concatenate((self._pcm_buf, samples))
            while len(self._pcm_buf) >= FRAME_SAMPLES:
                frame = self._pcm_buf[:FRAME_SAMPLES]
                self._pcm_buf = self._pcm_buf[FRAME_SAMPLES:]
                self._encode_and_send(frame)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _encode_and_send(self, frame: np.ndarray) -> None:
        pcm_bytes = (
            np.clip(frame, -1.0, 1.0) * 32767
        ).astype(np.int16).tobytes()
        try:
            assert self._encoder is not None
            encoded = self._encoder.encode(pcm_bytes, FRAME_SAMPLES)
        except Exception as exc:
            logger.debug("Opus encode error: %s", exc)
            return

        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        packet = _HDR.pack(seq, len(encoded)) + encoded

        dead: set[tuple] = set()
        with self._clients_lock:
            targets = list(self._clients)
        for addr in targets:
            try:
                assert self._sock is not None
                self._sock.sendto(packet, addr)
            except Exception:
                dead.add(addr)
        if dead:
            with self._clients_lock:
                self._clients -= dead
                for addr in dead:
                    logger.info("Audio client timed out, removed: %s", addr)

    def _recv_loop(self) -> None:
        """Accept AUDIO_HELLO / AUDIO_BYE registration datagrams."""
        assert self._sock is not None
        while self._running:
            try:
                data, addr = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == b"AUDIO_HELLO":
                with self._clients_lock:
                    self._clients.add(addr)
                logger.info("Audio client registered: %s", addr)
            elif data == b"AUDIO_BYE":
                with self._clients_lock:
                    self._clients.discard(addr)
                logger.info("Audio client deregistered: %s", addr)


class AudioUDPClient:
    """Remote-side Opus/UDP audio receiver and player.

    Registers with an AudioUDPServer, decodes incoming Opus frames,
    and plays them through the local default output via sounddevice with
    a jitter buffer of up to MAX_BUFFER_FRAMES × 20 ms ≈ 300 ms.

    Parameters
    ----------
    server_host:
        IP address of the shack machine.
    server_udp_port:
        UDP port the AudioUDPServer is listening on.
    sample_rate:
        Must match the server's sample rate (typically 48000).
    """

    def __init__(
        self,
        server_host: str,
        server_udp_port: int,
        sample_rate: int = 48000,
    ) -> None:
        self._server = (server_host, server_udp_port)
        self._sample_rate = sample_rate

        self._decoder = None
        self._sock: socket.socket | None = None
        self._stream = None            # sounddevice.OutputStream
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._pcm_callbacks: list = []

        # Jitter buffer: deque of decoded float32 PCM frames.
        # maxlen drops the oldest frames when we fall badly behind, preventing
        # unbounded memory growth and keeping latency bounded.
        self._buf: collections.deque = collections.deque(maxlen=MAX_BUFFER_FRAMES)
        self._partial = np.zeros(0, dtype=np.float32)
        self._buf_lock = threading.Lock()

    def add_pcm_callback(self, cb) -> None:
        self._pcm_callbacks.append(cb)

    def remove_pcm_callback(self, cb) -> None:
        try:
            self._pcm_callbacks.remove(cb)
        except ValueError:
            pass

    def start(self) -> None:
        """Open the UDP socket, register with the server, and start playback."""
        try:
            import opuslib
            import sounddevice as sd
        except ImportError as exc:
            logger.error("Audio client deps missing: %s", exc)
            return

        self._decoder = opuslib.Decoder(self._sample_rate, 1)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(1.0)
        self._sock.sendto(b"AUDIO_HELLO", self._server)

        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="audio-udp-client", daemon=True
        )
        self._recv_thread.start()

        self._stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("Audio client started, streaming from %s", self._server)

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.sendto(b"AUDIO_BYE", self._server)
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._recv_thread:
            self._recv_thread.join(timeout=2)
            self._recv_thread = None
        logger.info("Audio client stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _recv_loop(self) -> None:
        assert self._sock is not None
        assert self._decoder is not None
        while self._running:
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < _HDR.size:
                continue
            _seq, payload_len = _HDR.unpack_from(data)
            payload = data[_HDR.size: _HDR.size + payload_len]
            try:
                pcm_bytes = self._decoder.decode(payload, FRAME_SAMPLES)
                pcm = (
                    np.frombuffer(pcm_bytes, dtype=np.int16)
                    .astype(np.float32)
                    / 32767.0
                )
                with self._buf_lock:
                    self._buf.append(pcm)
                for cb in self._pcm_callbacks:
                    try:
                        cb(pcm)
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("Opus decode error: %s", exc)

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time_info, status
    ) -> None:
        """sounddevice OutputStream callback — runs in a C audio thread."""
        with self._buf_lock:
            filled = 0
            while filled < frames:
                if len(self._partial) > 0:
                    n = min(len(self._partial), frames - filled)
                    outdata[filled: filled + n, 0] = self._partial[:n]
                    self._partial = self._partial[n:]
                    filled += n
                elif self._buf:
                    self._partial = self._buf.popleft()
                else:
                    # Buffer underrun — play silence rather than stutter
                    outdata[filled:, 0] = 0.0
                    return
