"""Mode selection button panel."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from ..cat.base import Mode


_DISPLAY_MODES: list[tuple[str, Mode]] = [
    ("LSB", Mode.LSB),
    ("USB", Mode.USB),
    ("CW", Mode.CW),
    ("CW-R", Mode.CW_R),
    ("AM", Mode.AM),
    ("FM", Mode.FM),
    ("RTTY", Mode.RTTY_LSB),
    ("DATA", Mode.DATA_USB),
    ("C4FM", Mode.C4FM),
]

_BUTTON_STYLE = """
QPushButton {
    background-color: #1E2530;
    color: #AAAAAA;
    border: 1px solid #333;
    border-radius: 3px;
    padding: 3px 8px;
    font-size: 11px;
}
QPushButton:checked {
    background-color: #0066AA;
    color: #FFFFFF;
    border: 1px solid #0088CC;
}
QPushButton:hover:!checked {
    background-color: #2A3542;
}
"""


class ModePanel(QWidget):
    """Row of mode toggle buttons.

    Emits ``mode_selected(Mode)`` when a new mode is chosen.
    """

    mode_selected = Signal(object)  # Mode enum

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._mode_map: dict[Mode, QPushButton] = {}
        self._user_pending = False

        for label, mode in _DISPLAY_MODES:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(_BUTTON_STYLE)
            btn.clicked.connect(lambda checked, m=mode: self._on_clicked(m))
            self._group.addButton(btn)
            self._mode_map[mode] = btn
            layout.addWidget(btn)

        layout.addStretch()

        self._cooldown = QTimer(self)
        self._cooldown.setSingleShot(True)
        self._cooldown.timeout.connect(self._cooldown_expired)

    def set_mode(self, mode: Mode) -> None:
        if self._user_pending:
            return
        btn = self._mode_map.get(mode)
        if btn and not btn.isChecked():
            for b in self._mode_map.values():
                b.blockSignals(True)
            btn.setChecked(True)
            for b in self._mode_map.values():
                b.blockSignals(False)

    def _on_clicked(self, mode: Mode) -> None:
        self._user_pending = True
        self._cooldown.start(500)
        self.mode_selected.emit(mode)

    def _cooldown_expired(self) -> None:
        self._user_pending = False
