"""
Waterfall display widget.

Uses pyqtgraph's ImageItem rendered inside a PlotWidget for GPU-accelerated
display.  The widget wraps WaterfallBuffer and updates at the configured
frame rate via a QTimer.

Clicking on the waterfall emits ``freq_clicked(int)`` with the Hz offset
from the current VFO frequency.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

try:
    import pyqtgraph as pg
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

from ..spectrum.waterfall import WaterfallBuffer

# Built-in colour maps (fall back if pyqtgraph is missing a colourmap)
_COLORMAPS = {
    "plasma": None,
    "inferno": None,
    "turbo": None,
    "grayscale": None,
}


class WaterfallWidget(QWidget):
    """Scrolling waterfall display.

    Parameters
    ----------
    rows:
        History depth (time axis).
    cols:
        Frequency axis resolution.
    """

    freq_clicked = Signal(float)   # Hz offset from centre

    def __init__(
        self,
        rows: int = 512,
        cols: int = 1024,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._buffer = WaterfallBuffer(rows=rows, cols=cols)
        self._centre_hz: float = 14_074_000.0
        self._span_hz: float = 200_000.0   # visible bandwidth

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if _PG_AVAILABLE:
            self._setup_pyqtgraph(layout, rows, cols)
        else:
            from PySide6.QtWidgets import QLabel
            lbl = QLabel("pyqtgraph not installed — waterfall unavailable")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)
            self._image_item = None

        # Refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(67)   # ~15 fps
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_pyqtgraph(self, layout: QVBoxLayout, rows: int, cols: int) -> None:
        pg.setConfigOptions(antialias=False, useOpenGL=True)

        self._plot = pg.PlotWidget(background="#0D1117")
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.showGrid(x=False, y=False)
        self._plot.getAxis("left").hide()
        self._plot.getAxis("bottom").hide()

        self._image_item = pg.ImageItem()
        self._plot.addItem(self._image_item)

        # Colour map
        try:
            cmap = pg.colormap.get("plasma")
            self._image_item.setColorMap(cmap)
        except Exception:
            pass

        # Click handler
        self._plot.scene().sigMouseClicked.connect(self._on_mouse_click)

        layout.addWidget(self._plot)

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def push_line(self, data: np.ndarray | list[float]) -> None:
        """Add one spectral row (called from CAT or audio source)."""
        self._buffer.push_line(data)

    def set_centre(self, hz: float) -> None:
        self._centre_hz = hz

    def set_span(self, hz: float) -> None:
        self._span_hz = hz

    def set_db_range(self, db_min: float, db_max: float) -> None:
        self._buffer.set_range(db_min, db_max)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._image_item is None:
            return
        img = self._buffer.image()   # shape: (rows, cols)
        # pyqtgraph ImageItem expects (cols, rows) or (x, y)
        self._image_item.setImage(img.T, autoLevels=False, levels=(0.0, 1.0))

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _on_mouse_click(self, event) -> None:
        if not _PG_AVAILABLE:
            return
        pos = event.scenePos()
        view_pos = self._plot.plotItem.vb.mapSceneToView(pos)
        # Map pixel x → Hz offset
        cols = self._buffer.cols
        frac = view_pos.x() / cols
        hz_offset = (frac - 0.5) * self._span_hz
        self.freq_clicked.emit(self._centre_hz + hz_offset)
