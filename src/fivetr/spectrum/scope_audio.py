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

        # hop = how many new samples between successive FFTs
        # Derived from frame_rate so display updates match the requested FPS.
        self._hop = max(1, int(sample_rate / frame_rate))

        # Sliding-window accumulator.  We append incoming samples here and
        # emit an FFT every _hop samples once we have fft_size samples.
        self._accum = np.zeros(0, dtype=np.float32)

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

        # blocksize controls latency; 512–2048 is a good range.
        # Use the smaller of hop and 1024 so callbacks arrive frequently enough.
        blocksize = min(self._hop, 1024)
        self._accum = np.zeros(0, dtype=np.float32)  # reset on (re)start
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
        """Called by sounddevice in a background thread.

        Uses a sliding window accumulator: append incoming samples, then emit
        one FFT frame for every ``_hop`` new samples once the buffer is full.
        This avoids the dead-zone bug in a classic ring-buffer approach and
        correctly honours the requested frame_rate.
        """
        if not self._running:
            return

        # Mix to mono if stereo
        mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()

        # Grow the accumulator
        self._accum = np.concatenate((self._accum, mono))

        # Emit one FFT per hop worth of new samples
        while len(self._accum) >= self.fft_size:
            self._emit_fft(self._accum[:self.fft_size])
            # Advance by one hop (overlap = keep fft_size - hop samples)
            self._accum = self._accum[self._hop:]

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
