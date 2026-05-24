"""
Waterfall display widget.

Uses pyqtgraph's ImageItem rendered inside a PlotWidget.
Data is pushed via push_line() which MUST be called from the main thread
(use a Qt Signal to cross thread boundaries from audio/CAT callbacks).

pyqtgraph 0.14 notes:
  - levels must be set via setLevels() / setOpts(), not as setImage() kwargs
  - ImageItem pixel coordinates start at (0,0) bottom-left; we disable
    mouse panning and lock the view range manually so the image always fills
    the widget.
  - OpenGL is disabled by default — it causes rendering issues on some
    Linux/Mesa drivers without hurting waterfall performance noticeably.
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

try:
    import pyqtgraph as pg
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False
    logger.warning("pyqtgraph not installed — waterfall unavailable")

from ..spectrum.waterfall import WaterfallBuffer


def _make_lut(name: str = "plasma") -> np.ndarray:
    """Return a 256×4 uint8 LUT for the requested colourmap.

    Falls back to a hand-crafted blue→cyan→yellow→red LUT if the named
    colourmap is unavailable.
    """
    if _PG_AVAILABLE:
        try:
            cmap = pg.colormap.get(name)
            return cmap.getLookupTable(nPts=256, alpha=False)
        except Exception:
            pass
    # Fallback: 4-stop gradient (dark blue → cyan → yellow → red)
    stops = np.array([
        [0,   0,   0,  80],   # dark navy
        [0,   0, 255, 255],   # blue
        [0, 255, 255, 255],   # cyan
        [255, 255, 0, 255],   # yellow
        [255,  0,  0, 255],   # red
    ], dtype=np.uint8)
    lut = np.zeros((256, 3), dtype=np.uint8)
    n = len(stops) - 1
    for i in range(256):
        t = i / 255.0 * n
        idx = int(t)
        frac = t - idx
        if idx >= n:
            lut[i] = stops[-1, 1:]
        else:
            lut[i] = (stops[idx, 1:] * (1 - frac) + stops[idx + 1, 1:] * frac).astype(np.uint8)
    return lut


class WaterfallWidget(QWidget):
    """Scrolling waterfall display.

    Call ``push_line(data)`` from the **main thread** each time a new
    spectral row is available.  Use a Qt Signal (QueuedConnection) to
    cross thread boundaries from audio or CAT callbacks.

    Parameters
    ----------
    rows:   History depth (time axis, scrolls downward).
    cols:   Frequency bins per row.
    """

    freq_clicked = Signal(float)   # emits Hz when user clicks waterfall

    def __init__(
        self,
        rows: int = 512,
        cols: int = 1024,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._rows = rows
        self._cols = cols
        self._buffer = WaterfallBuffer(rows=rows, cols=cols)
        self._centre_hz: float = 14_074_000.0
        self._span_hz: float = 192_000.0    # typical 48 kHz audio → ±96 kHz
        self._dirty = False                  # true when buffer has new data
        self._range_set = False             # have we locked the view range yet?

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if _PG_AVAILABLE:
            self._image_item: pg.ImageItem | None = None
            self._setup_pyqtgraph(layout)
        else:
            lbl = QLabel("pyqtgraph not installed — waterfall unavailable")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)
            self._image_item = None

        # Refresh timer — fires on the main thread
        self._timer = QTimer(self)
        self._timer.setInterval(67)          # ~15 fps
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_pyqtgraph(self, layout: QVBoxLayout) -> None:
        # Disable OpenGL — avoids rendering bugs on Mesa/X11 without a
        # meaningful performance cost for a scrolling 2-D image.
        pg.setConfigOptions(antialias=False, useOpenGL=False)

        self._plot = pg.PlotWidget(background="#0D1117")
        pi = self._plot.getPlotItem()

        # Remove all axes / padding so the image fills the widget edge-to-edge
        pi.hideAxis("left")
        pi.hideAxis("bottom")
        pi.setContentsMargins(0, 0, 0, 0)
        pi.layout.setContentsMargins(0, 0, 0, 0)

        # Disable interactive pan/zoom — waterfall is read-only for now
        self._plot.setMouseEnabled(x=False, y=False)

        self._image_item = pg.ImageItem()
        pi.addItem(self._image_item)

        # Apply LUT (lookup table) — more reliable than setColorMap in 0.14
        lut = _make_lut("plasma")
        self._image_item.setLookupTable(lut)

        # Set display range [0.0, 1.0] — our buffer always normalises to this
        self._image_item.setLevels([0.0, 1.0])

        # Click-to-tune
        self._plot.scene().sigMouseClicked.connect(self._on_mouse_click)

        layout.addWidget(self._plot)

    # ------------------------------------------------------------------
    # Public API — MUST be called from the main thread
    # ------------------------------------------------------------------

    @Slot(object)
    def push_line(self, data: np.ndarray | list[float]) -> None:
        """Push one row of spectral data (dB values or normalised floats).

        Decorated as a Slot so Qt routes cross-thread signal emissions onto
        the main thread automatically when connected with QueuedConnection
        (the default for signals crossing thread boundaries).
        """
        self._buffer.push_line(data)
        self._dirty = True

    def set_centre(self, hz: float) -> None:
        self._centre_hz = hz

    def set_span(self, hz: float) -> None:
        self._span_hz = hz

    def set_db_range(self, db_min: float, db_max: float) -> None:
        self._buffer.set_range(db_min, db_max)

    def set_colormap(self, name: str) -> None:
        if self._image_item is None:
            return
        lut = _make_lut(name)
        self._image_item.setLookupTable(lut)

    # ------------------------------------------------------------------
    # Refresh (main thread, via QTimer)
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._image_item is None or not self._dirty:
            return

        # image() returns (rows, cols) float32, newest row last (bottom)
        img = self._buffer.image()

        # pyqtgraph ImageItem treats the first axis as x (horizontal) and
        # the second as y (vertical).  Transpose so cols → x, rows → y.
        self._image_item.setImage(img.T, autoLevels=False)

        # On first data, lock the view range to exactly cover the image.
        # Without this the ImageItem is rendered at pixel-space coordinates
        # that the ViewBox may not be showing.
        if not self._range_set:
            vb = self._plot.getPlotItem().getViewBox()
            vb.setRange(
                xRange=(0, self._cols),
                yRange=(0, self._rows),
                padding=0,
                update=True,
            )
            vb.setLimits(
                xMin=0, xMax=self._cols,
                yMin=0, yMax=self._rows,
            )
            self._range_set = True

        self._dirty = False

    # ------------------------------------------------------------------
    # Mouse click → freq_clicked signal
    # ------------------------------------------------------------------

    def _on_mouse_click(self, event) -> None:
        if self._image_item is None:
            return
        pos = event.scenePos()
        vb = self._plot.getPlotItem().getViewBox()
        view_pos = vb.mapSceneToView(pos)
        # Map x pixel (0…cols) → Hz
        frac = view_pos.x() / self._cols
        hz = self._centre_hz + (frac - 0.5) * self._span_hz
        self.freq_clicked.emit(hz)
