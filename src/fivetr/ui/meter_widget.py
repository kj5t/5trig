"""
S-meter / RF power / SWR bar meter widget.

Draws a segmented analogue-style bar with zone colouring
(green S1–S9, yellow S9+20, red S9+40+).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..cat.base import Meters


class MeterWidget(QWidget):
    """Segmented level meter.

    Parameters
    ----------
    meter_type:
        Which meter to display (determines scale labels).
    """

    _BG = QColor("#0D1117")
    _BORDER = QColor("#333333")
    _SEG_OFF = QColor("#1A2030")
    _GREEN = QColor("#00AA44")
    _YELLOW = QColor("#DDAA00")
    _RED = QColor("#CC2200")

    def __init__(
        self,
        meter_type: Meters = Meters.SMETER,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._type = meter_type
        self._value: float = 0.0   # 0.0–1.0 normalised
        self.setMinimumSize(200, 40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(50)

    def set_value(self, normalised: float) -> None:
        """Set meter value.  ``normalised`` should be 0.0–1.0."""
        self._value = max(0.0, min(1.0, normalised))
        self.update()

    def set_type(self, t: Meters) -> None:
        self._type = t
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        seg_area_top = 16
        seg_area_h = h - seg_area_top - 4

        p.fillRect(self.rect(), self._BG)

        # Title
        font = QFont("Monospace", 8)
        p.setFont(font)
        p.setPen(QPen(QColor("#888888")))
        p.drawText(4, 12, self._type_label())

        # Scale marks
        self._draw_scale(p, seg_area_top, w, seg_area_h)

        # Segments
        n_segs = 30
        seg_w = max(4, (w - 8) // n_segs - 1)
        active = int(self._value * n_segs)

        for i in range(n_segs):
            x = 4 + i * ((w - 8) // n_segs)
            y = seg_area_top + 2

            if i < active:
                if i < 15:
                    col = self._GREEN
                elif i < 22:
                    col = self._YELLOW
                else:
                    col = self._RED
            else:
                col = self._SEG_OFF

            p.fillRect(x, y, seg_w, seg_area_h - 4, col)

        p.end()

    def _type_label(self) -> str:
        labels = {
            Meters.SMETER: "S",
            Meters.POWER: "PWR",
            Meters.ALC: "ALC",
            Meters.SWR: "SWR",
            Meters.COMP: "COMP",
            Meters.VD: "VD",
            Meters.ID: "ID",
        }
        return labels.get(self._type, "?")

    def _draw_scale(
        self, p: QPainter, top: int, width: int, height: int
    ) -> None:
        if self._type == Meters.SMETER:
            marks = [(i / 9, f"S{i}") for i in range(1, 10)] + [
                (9.25 / 10.5, "+20"),
                (9.75 / 10.5, "+40"),
            ]
        else:
            marks = [(i / 10, f"{i*10}") for i in range(0, 11)]

        font = QFont("Monospace", 7)
        p.setFont(font)
        p.setPen(QPen(QColor("#555555")))

        for frac, label in marks:
            x = int(4 + frac * (width - 8))
            p.drawLine(x, top, x, top + 4)
            p.drawText(x - 8, top - 1, label)
