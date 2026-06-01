"""
Audio-based spectrum source.

Captures audio from the radio's USB sound card (or any sound device),
runs a real-time FFT, and emits normalized power arrays at a configurable
frame rate.

Two capture backends are supported:

* **sounddevice** (default) — opens the device via ALSA/PortAudio.  Works
  on all platforms but takes an exclusive ALSA lock on Linux hw:X,0 devices,
  which evicts PipeWire and silences the monitoring path.

* **PipeWire subprocess** — spawns ``pw-record --target=<node>`` and reads
  raw float32 samples from its stdout.  PipeWire stays in charge of the
  device so the monitoring path remains intact.  Preferred on Linux whenever
  PipeWire is running.

Used by FT-710 which lacks CAT-based scope, and as a fallback for the
FTdx-10/101 when scope CAT data is unavailable.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# Type alias for the consumer callback
ScopeCallback = Callable[[np.ndarray], None]


class AudioScopeSource:
    """Captures audio and emits FFT magnitude arrays.

    Parameters
    ----------
    device:
        sounddevice device index or name.  Pass ``None`` to use the system
        default.  Ignored when *pw_node* is set.
    pw_node:
        PipeWire node name (e.g. the ``alsa_input.usb-…`` name shown by
        ``pactl list sources short``).  When non-empty the PipeWire
        subprocess backend is used instead of sounddevice; this avoids the
        exclusive ALSA lock that breaks speaker monitoring on Linux.
    sample_rate:
        Sample rate in Hz.  Must match what the radio outputs (typically 48000).
    fft_size:
        Number of FFT bins.  More bins = finer frequency resolution.
    overlap:
        Fraction of overlap between successive windows (0.0–0.9).
    frame_rate:
        Target display frames per second.
    """

    def __init__(
        self,
        device: int | str | None = None,
        pw_node: str = "",
        sample_rate: int = 48000,
        fft_size: int = 4096,
        overlap: float = 0.5,
        frame_rate: float = 15.0,
    ) -> None:
        self.device = device
        self.pw_node = pw_node
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.overlap = overlap
        self.frame_rate = frame_rate

        self._callbacks: list[ScopeCallback] = []
        self._pcm_callbacks: list[ScopeCallback] = []
        self._running = False

        # sounddevice backend state
        self._stream = None

        # PipeWire subprocess backend state
        self._pw_proc: subprocess.Popen | None = None
        self._pw_thread: threading.Thread | None = None

        # Pre-compute Hanning window
        self._window = np.hanning(fft_size).astype(np.float32)

        # hop = how many new samples between successive FFTs
        # Derived from frame_rate so display updates match the requested FPS.
        self._hop = max(1, int(sample_rate / frame_rate))

        # Sliding-window accumulator.  We append incoming samples here and
        # emit an FFT every _hop samples once we have fft_size samples.
        self._accum = np.zeros(0, dtype=np.float32)
        self._accum_lock = threading.Lock()

    def add_callback(self, cb: ScopeCallback) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: ScopeCallback) -> None:
        self._callbacks.remove(cb)

    def add_pcm_callback(self, cb: ScopeCallback) -> None:
        """Register a callback for raw mono float32 PCM chunks (variable size).

        Called from the audio capture thread before FFT processing.  Use this
        to feed AudioUDPServer.on_pcm() for remote audio streaming.
        """
        self._pcm_callbacks.append(cb)

    def remove_pcm_callback(self, cb: ScopeCallback) -> None:
        try:
            self._pcm_callbacks.remove(cb)
        except ValueError:
            pass

    def start(self) -> None:
        """Start the audio capture stream."""
        self._accum = np.zeros(0, dtype=np.float32)
        self._running = True

        if self.pw_node:
            self._start_pipewire()
        else:
            self._start_sounddevice()

    def stop(self) -> None:
        self._running = False

        # Stop sounddevice stream
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Stop pw-record process
        if self._pw_proc:
            try:
                self._pw_proc.terminate()
                self._pw_proc.wait(timeout=2)
            except Exception:
                pass
            self._pw_proc = None

        if self._pw_thread:
            self._pw_thread.join(timeout=3)
            self._pw_thread = None

    # ------------------------------------------------------------------
    # sounddevice backend
    # ------------------------------------------------------------------

    def _start_sounddevice(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed; audio scope unavailable")
            return

        blocksize = min(self._hop, 1024)
        try:
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info(
                "Audio scope started (sounddevice): device=%s sr=%d fft=%d",
                self.device, self.sample_rate, self.fft_size,
            )
        except Exception as exc:
            logger.error("sounddevice open failed: %s", exc)

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """Called by sounddevice in a background thread."""
        if not self._running:
            return
        mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
        self._process_samples(mono)

    # ------------------------------------------------------------------
    # PipeWire subprocess backend
    # ------------------------------------------------------------------

    def _start_pipewire(self) -> None:
        cmd = [
            "pw-record",
            f"--target={self.pw_node}",
            f"--rate={self.sample_rate}",
            "--channels=1",
            "--format=f32",
            "-",   # write raw samples to stdout
        ]
        try:
            self._pw_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("pw-record not found; falling back to sounddevice")
            self._start_sounddevice()
            return

        self._pw_thread = threading.Thread(
            target=self._read_pipewire,
            name="pw-record-reader",
            daemon=True,
        )
        self._pw_thread.start()
        logger.info(
            "Audio scope started (pw-record): target=%s sr=%d fft=%d",
            self.pw_node, self.sample_rate, self.fft_size,
        )

    def _read_pipewire(self) -> None:
        """Read raw f32 samples from pw-record stdout in a daemon thread."""
        CHUNK_BYTES = 4096          # 1024 float32 samples per read
        buf = b""
        assert self._pw_proc is not None
        assert self._pw_proc.stdout is not None

        while self._running:
            try:
                chunk = self._pw_proc.stdout.read(CHUNK_BYTES)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            n_floats = len(buf) // 4
            if n_floats < 128:      # wait for a reasonable minimum chunk
                continue
            arr = np.frombuffer(buf[: n_floats * 4], dtype=np.float32).copy()
            buf = buf[n_floats * 4 :]
            # Sanitize: replace NaN/Inf from pw-record header with 0
            np.nan_to_num(arr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            self._process_samples(arr)

        logger.debug("pw-record reader thread exiting")

    # ------------------------------------------------------------------
    # Shared sample processing (both backends funnel here)
    # ------------------------------------------------------------------

    def _process_samples(self, mono: np.ndarray) -> None:
        """Append *mono* to the accumulator and emit FFTs as they fill up."""
        # Emit raw PCM to any streaming consumers before the FFT path.
        # Called outside _accum_lock to avoid holding it during callbacks.
        for cb in self._pcm_callbacks:
            try:
                cb(mono)
            except Exception:
                logger.exception("PCM callback error")

        with self._accum_lock:
            self._accum = np.concatenate((self._accum, mono))
            while len(self._accum) >= self.fft_size:
                self._emit_fft(self._accum[: self.fft_size])
                self._accum = self._accum[self._hop :]

    def _emit_fft(self, samples: np.ndarray) -> None:
        windowed = samples * self._window
        spectrum = np.fft.rfft(windowed)
        magnitude = np.abs(spectrum)

        # Normalise for FFT length and window amplitude so 0 dBFS = 0 dB.
        # Dividing by sum(window)/2 converts raw bin magnitude → peak amplitude
        # in the range [0, 1] for a full-scale input signal, making the dB
        # values comparable to dBFS regardless of fft_size.
        win_sum = float(np.sum(self._window)) / 2.0   # /2 for one-sided spectrum
        magnitude = magnitude / (win_sum + 1e-30)

        # Convert to dB, floor at -120 dBFS
        with np.errstate(divide="ignore"):
            db = 20.0 * np.log10(magnitude + 1e-12)
        db = np.clip(db, -120.0, 0.0)

        for cb in self._callbacks:
            try:
                cb(db)
            except Exception:
                logger.exception("Scope callback error")

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices() -> list[dict]:
        """Return list of available audio devices (sounddevice)."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            return [
                {"index": i, "name": d["name"], "inputs": d["max_input_channels"]}
                for i, d in enumerate(devices)
                if d["max_input_channels"] > 0
            ]
        except Exception:
            return []

    @staticmethod
    def list_pw_sources() -> list[str]:
        """Return PipeWire capture source node names from ``pactl list sources``.

        Returns an empty list if PipeWire / pactl is not available.
        """
        try:
            out = subprocess.check_output(
                ["pactl", "list", "sources", "short"],
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).decode()
        except Exception:
            return []
        sources = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1]
                # Exclude monitor sources (playback monitor, not real input)
                if not name.endswith(".monitor"):
                    sources.append(name)
        return sources

    @staticmethod
    def detect_pw_node_for_device(sd_device: int | str | None) -> str:
        """Try to find the PipeWire node name that corresponds to *sd_device*.

        Uses the ALSA card name from sounddevice to match against PipeWire
        source names.  Returns empty string if no match or PipeWire unavailable.
        """
        try:
            import sounddevice as sd
            info = sd.query_devices(sd_device)
            dev_name: str = info["name"]  # e.g. "USB AUDIO  CODEC: Audio (hw:2,0)"
        except Exception:
            return ""

        # Extract hw card number, e.g. "hw:2,0" → "2"
        import re
        m = re.search(r"hw:(\d+)", dev_name)
        card_id = m.group(1) if m else None

        sources = AudioScopeSource.list_pw_sources()
        # Strategy: prefer sources that match partial device name tokens
        name_tokens = [t.lower() for t in re.split(r"[\s:,()]+", dev_name) if len(t) > 3]

        best = ""
        best_score = 0
        for src in sources:
            src_lower = src.lower()
            score = sum(1 for tok in name_tokens if tok in src_lower)
            if card_id and card_id in src_lower:
                score += 2
            if score > best_score:
                best_score = score
                best = src
        return best
