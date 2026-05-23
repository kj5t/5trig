"""
Frequency display widget.

Renders a segmented-LCD style 9-digit frequency readout with colour-coded
groups (MHz · kHz · Hz).  Clicking a digit group raises a DirectEntryDialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class FreqDisplay(QWidget):
    """Clickable segmented frequency display.

    Emits ``freq_entered(int)`` when the user types a new frequency.
    """

    freq_entered = Signal(int)   # Hz

    _MHZ_COLOR = QColor("#00CFFF")
    _KHZ_COLOR = QColor("#00CFFF")
    _HZ_COLOR = QColor("#007AAA")
    _BG_COLOR = QColor("#0D1117")
    _FONT_FAMILY = "DS-Digital"   # falls back to monospace

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hz: int = 0
        self._vfo_label: str = "VFO A"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(60)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_font()

    def _setup_font(self) -> None:
        self._font = QFont(self._FONT_FAMILY, 36, QFont.Weight.Bold)
        self._font.setStyleHint(QFont.StyleHint.Monospace)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_freq(self, hz: int) -> None:
        self._hz = hz
        self.update()

    def set_vfo_label(self, label: str) -> None:
        self._vfo_label = label
        self.update()

    def freq(self) -> int:
        return self._hz

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_entry_dialog()

    def _open_entry_dialog(self) -> None:
        dlg = _FreqEntryDialog(self._hz, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            hz = dlg.result_hz()
            if hz > 0:
                self._hz = hz
                self.update()
                self.freq_entered.emit(hz)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self._BG_COLOR)

        mhz = self._hz // 1_000_000
        khz = (self._hz % 1_000_000) // 1_000
        hz = self._hz % 1_000

        text = f"{mhz:3d}.{khz:03d}.{hz:03d}"

        p.setFont(self._font)

        # VFO label (small, top-left)
        label_font = QFont(self._FONT_FAMILY, 10)
        label_font.setStyleHint(QFont.StyleHint.Monospace)
        p.setFont(label_font)
        p.setPen(QPen(QColor("#666666")))
        p.drawText(6, 18, self._vfo_label)

        # Main frequency text
        p.setFont(self._font)
        fm = p.fontMetrics()
        char_w = fm.horizontalAdvance("0")
        y = self.height() - 8

        col = 8
        # Draw each character with group colouring
        for i, ch in enumerate(text):
            if ch == ".":
                p.setPen(QPen(QColor("#444444")))
            elif i < 3:
                p.setPen(QPen(self._MHZ_COLOR))
            elif i < 7:
                p.setPen(QPen(self._KHZ_COLOR))
            else:
                p.setPen(QPen(self._HZ_COLOR))
            p.drawText(col, y, ch)
            col += char_w + 1

        p.end()


class _FreqEntryDialog(QDialog):
    """Simple frequency entry dialog."""

    def __init__(self, current_hz: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enter Frequency")
        self._hz = 0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Frequency (MHz):"))
        self._edit = QLineEdit()
        self._edit.setText(f"{current_hz / 1_000_000:.6f}")
        self._edit.selectAll()
        layout.addWidget(self._edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._edit.returnPressed.connect(self._on_accept)

    def _on_accept(self) -> None:
        try:
            mhz = float(self._edit.text().replace(",", ".").strip())
            self._hz = int(mhz * 1_000_000)
            self.accept()
        except ValueError:
            self._edit.setStyleSheet("border: 1px solid red;")

    def result_hz(self) -> int:
        return self._hz
