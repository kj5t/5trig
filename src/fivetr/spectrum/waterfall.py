"""
Waterfall data buffer.

Maintains a 2-D numpy array of (time × frequency) power values suitable for
display via pyqtgraph's ImageItem.  Both CAT-based scope data and audio FFT
data are accepted via push_line().
"""

from __future__ import annotations

import numpy as np


class WaterfallBuffer:
    """Ring buffer storing waterfall history.

    Parameters
    ----------
    rows:
        Number of time rows to keep (scroll history).
    cols:
        Number of frequency bins per row.
    db_min, db_max:
        Clipping range for normalisation to [0.0, 1.0].
    """

    def __init__(
        self,
        rows: int = 512,
        cols: int = 1024,
        db_min: float = -120.0,
        db_max: float = -40.0,
    ) -> None:
        self.rows = rows
        self.cols = cols
        self.db_min = db_min
        self.db_max = db_max

        # image[0] is the newest row (top of waterfall)
        self._buf: np.ndarray = np.zeros((rows, cols), dtype=np.float32)
        self._write_row = 0

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def push_line(self, data: np.ndarray | list[float]) -> None:
        """Add one row of spectral data.

        ``data`` may have any length; it will be resampled to ``self.cols``.
        Values are assumed to be in dB (negative, with 0 dBFS = 0.0).
        """
        arr = np.asarray(data, dtype=np.float32)

        # Resample to target column count
        if len(arr) != self.cols:
            x_old = np.linspace(0, 1, len(arr))
            x_new = np.linspace(0, 1, self.cols)
            arr = np.interp(x_new, x_old, arr).astype(np.float32)

        # Normalise to [0, 1]
        norm = (arr - self.db_min) / (self.db_max - self.db_min)
        norm = np.clip(norm, 0.0, 1.0)

        self._buf[self._write_row] = norm
        self._write_row = (self._write_row + 1) % self.rows

    # ------------------------------------------------------------------
    # Display interface
    # ------------------------------------------------------------------

    def image(self) -> np.ndarray:
        """Return the waterfall as a (rows × cols) float32 array, newest last.

        pyqtgraph's ImageItem displays row 0 at the bottom, so we roll the
        buffer so the most recent data appears at the top of the returned
        array (row 0).
        """
        # Roll so newest row is at index 0
        rolled = np.roll(self._buf, -self._write_row, axis=0)
        # Flip vertically: newest row → bottom of display
        return rolled[::-1]

    def set_range(self, db_min: float, db_max: float) -> None:
        """Adjust the colour mapping range."""
        self.db_min = db_min
        self.db_max = db_max

    def resize(self, rows: int | None = None, cols: int | None = None) -> None:
        """Resize the buffer (clears history)."""
        if rows is not None:
            self.rows = rows
        if cols is not None:
            self.cols = cols
        self._buf = np.zeros((self.rows, self.cols), dtype=np.float32)
        self._write_row = 0
