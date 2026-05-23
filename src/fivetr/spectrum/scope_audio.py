"""
Audio-based spectrum source.

Captures audio from the radio's USB sound card (or any sound device),
runs a real-time FFT, and emits normalized power arrays at a configurable
frame rate.

Used by FT-710 which lacks CAT-based scope, and as a fallback for the
FTdx-10/101 when scope CAT data is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
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
        sounddevice device index or name. Pass None to use the system default.
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
        sample_rate: int = 48000,
        fft_size: int = 4096,
        overlap: float = 0.5,
        frame_rate: float = 15.0,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.overlap = overlap
        self.frame_rate = frame_rate

        self._callbacks: list[ScopeCallback] = []
        self._running = False
        self._stream = None

        # Pre-compute Hanning window
        self._window = np.hanning(fft_size).astype(np.float32)
        # Accumulation buffer
        self._buf = np.zeros(fft_size * 2, dtype=np.float32)
        self._buf_pos = 0
        self._hop = int(fft_size * (1.0 - overlap))

    def add_callback(self, cb: ScopeCallback) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: ScopeCallback) -> None:
        self._callbacks.remove(cb)

    def start(self) -> None:
        """Start the audio capture stream (blocking call; run in executor)."""
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed; audio scope unavailable")
            return

        blocksize = max(self._hop, 256)
        self._running = True
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
            "Audio scope started: device=%s sr=%d fft=%d",
            self.device, self.sample_rate, self.fft_size,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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

        # Mix to mono if stereo
        mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()

        # Append to ring buffer
        n = len(mono)
        end = self._buf_pos + n
        if end <= len(self._buf):
            self._buf[self._buf_pos:end] = mono
        else:
            # Wrap around
            first = len(self._buf) - self._buf_pos
            self._buf[self._buf_pos:] = mono[:first]
            self._buf[:end - len(self._buf)] = mono[first:]
        self._buf_pos = end % len(self._buf)

        # Emit FFT frame when we have enough samples
        if self._buf_pos >= self.fft_size:
            chunk = self._buf[self._buf_pos - self.fft_size:self._buf_pos]
            self._emit_fft(chunk)

    def _emit_fft(self, samples: np.ndarray) -> None:
        windowed = samples * self._window
        spectrum = np.fft.rfft(windowed)
        magnitude = np.abs(spectrum)
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
        """Return list of available audio devices."""
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
