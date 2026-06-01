"""CW memory macro bar — a row of 8 programmable message buttons + Stop."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class MacroWidget(QWidget):
    """Row of 8 CW memory macro buttons and a Stop button.

    Signals
    -------
    macro_triggered(int):
        Emitted with slot index 0–7 when the user clicks a button or
        presses the corresponding F-key.
    stop_triggered:
        Emitted when the Stop button is clicked.
    """

    macro_triggered = Signal(int)
    stop_triggered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._buttons: list[QPushButton] = []
        for i in range(8):
            btn = QPushButton(f"F{i + 1}")
            btn.setMaximumWidth(70)
            btn.setEnabled(False)
            btn.clicked.connect(lambda _, idx=i: self.macro_triggered.emit(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setMaximumWidth(64)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton { color: #CC4444; }"
            "QPushButton:hover { background-color: #2D1010; }"
        )
        self._stop_btn.clicked.connect(self.stop_triggered)
        layout.addWidget(self._stop_btn)
        layout.addStretch()

    def apply_macros(self, macros: list, callsign: str = "") -> None:
        """Update button labels and tooltips from a list of CWMacro objects."""
        for i, btn in enumerate(self._buttons):
            if i < len(macros):
                m = macros[i]
                label = m.label.strip() or f"F{i + 1}"
                text = m.text.strip()
                preview = text.replace("{CALL}", callsign or "{CALL}")
                btn.setText(label)
                btn.setToolTip(f"F{i + 1}: {preview}" if preview else "")
                btn.setEnabled(bool(text))
            else:
                btn.setText(f"F{i + 1}")
                btn.setToolTip("")
                btn.setEnabled(False)

    def set_active(self, enabled: bool) -> None:
        """Enable or disable the Stop button (enabled while WK is transmitting)."""
        self._stop_btn.setEnabled(enabled)
