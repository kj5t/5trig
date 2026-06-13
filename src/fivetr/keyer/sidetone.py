"""Software CW sidetone generator.

Produces a gated sine wave through the default audio output when
the key is down.  Uses a short envelope to avoid clicks.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
BLOCK_SIZE = 256
RAMP_MS = 3


class SidetoneGenerator:
    """Generates a sine-wave sidetone gated by key state.

    Parameters
    ----------
    freq:
        Tone frequency in Hz (default 700).
    volume:
        Output level 0.0–1.0 (default 0.5).
    """

    def __init__(self, freq: int = 700, volume: float = 0.5) -> None:
        self._freq = freq
        self._volume = volume
        self._stream = None
        self._phase = 0.0
        self._keyed = False
        self._envelope = 0.0
        self._ramp_samples = max(1, int(SAMPLE_RATE * RAMP_MS / 1000))
        self._ramp_step = 1.0 / self._ramp_samples
        self._lock = threading.Lock()

    @property
    def freq(self) -> int:
        return self._freq

    @freq.setter
    def freq(self, hz: int) -> None:
        self._freq = max(200, min(1200, hz))

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed; sidetone unavailable")
            return
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("Sidetone started: %d Hz, volume %.0f%%", self._freq, self._volume * 100)

    def stop(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._keyed = False
        self._envelope = 0.0

    def key(self, down: bool) -> None:
        with self._lock:
            self._keyed = down

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time_info, status
    ) -> None:
        with self._lock:
            keyed = self._keyed
        phase_inc = 2.0 * np.pi * self._freq / SAMPLE_RATE
        out = outdata[:, 0]
        for i in range(frames):
            if keyed:
                self._envelope = min(1.0, self._envelope + self._ramp_step)
            else:
                self._envelope = max(0.0, self._envelope - self._ramp_step)
            out[i] = np.sin(self._phase) * self._envelope * self._volume
            self._phase += phase_inc
        if self._phase > 2.0 * np.pi:
            self._phase -= 2.0 * np.pi
