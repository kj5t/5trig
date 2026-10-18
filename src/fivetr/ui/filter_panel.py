"""Receive-filter controls: IF width, NAR and IF shift."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from ..cat.base import Mode, RadioState
from ..cat.filters import IF_SHIFT_MAX, IF_SHIFT_STEP, widths_for_mode
from .mode_buttons import _BUTTON_STYLE

# After the user touches a control, ignore the radio's reported value for this long, so a
# poll reply that was already in flight can't bounce the control back.
HOLD_OFF_S = 1.5
SHIFT_DEBOUNCE_MS = 60


class FilterPanel(QWidget):
    """Width / NAR / IF-shift row.

    Hidden until :meth:`update_state` sees a radio that reports filter settings.
    Emits ``width_selected(index)``, ``narrow_toggled(on)`` and ``shift_changed(hz)``
    only for changes the user makes, never for updates from the radio.
    """

    width_selected = Signal(int)     # SH index; 0 = the radio's default for the mode
    narrow_toggled = Signal(bool)
    shift_changed = Signal(int)      # Hz

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode: Mode | None = None
        self._touched: dict[str, float] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Filter"))
        self._width = QComboBox()
        self._width.setMinimumWidth(110)
        self._width.setToolTip("Receive IF bandwidth (SH)")
        self._width.activated.connect(self._on_width_activated)
        layout.addWidget(self._width)

        self._narrow = QPushButton("NAR")
        self._narrow.setCheckable(True)
        self._narrow.setStyleSheet(_BUTTON_STYLE)
        self._narrow.setToolTip("Narrow filter")
        self._narrow.clicked.connect(self._on_narrow_clicked)
        layout.addWidget(self._narrow)

        layout.addSpacing(12)
        layout.addWidget(QLabel("IF shift"))
        self._shift = QSlider(Qt.Orientation.Horizontal)
        steps = IF_SHIFT_MAX // IF_SHIFT_STEP
        self._shift.setRange(-steps, steps)
        self._shift.setMinimumWidth(180)
        self._shift.setToolTip("IF shift")
        self._shift.valueChanged.connect(self._on_shift_moved)
        layout.addWidget(self._shift)
        self._shift_label = QLabel("0 Hz")
        self._shift_label.setMinimumWidth(64)
        layout.addWidget(self._shift_label)
        centre = QPushButton("0")
        centre.setMaximumWidth(28)
        centre.setToolTip("Centre IF shift")
        centre.clicked.connect(lambda: self._shift.setValue(0))
        layout.addWidget(centre)
        layout.addStretch()

        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(SHIFT_DEBOUNCE_MS)
        self._send_timer.timeout.connect(self._send_shift)

        self.setVisible(False)

    # ------------------------------------------------------------------
    # From the radio
    # ------------------------------------------------------------------

    def update_state(self, state: RadioState) -> None:
        supported = not (
            state.filter_width_idx is None
            and state.if_shift_hz is None
            and state.narrow is None
        )
        self.setVisible(supported)
        if not supported:
            return
        self._rebuild_widths(state.mode)
        if state.filter_width_idx is not None and self._settled("width"):
            i = self._width.findData(state.filter_width_idx)
            if i >= 0:
                self._set_quiet(self._width, lambda w: w.setCurrentIndex(i))
        if state.narrow is not None and self._settled("narrow"):
            self._set_quiet(self._narrow, lambda w: w.setChecked(state.narrow))
        if (state.if_shift_hz is not None and self._settled("shift")
                and not self._shift.isSliderDown()):
            self._set_quiet(self._shift,
                            lambda w: w.setValue(state.if_shift_hz // IF_SHIFT_STEP))
            self._shift_label.setText(self._fmt_shift(state.if_shift_hz))

    def _rebuild_widths(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        table = widths_for_mode(mode)
        self._width.blockSignals(True)
        self._width.clear()
        self._width.setEnabled(bool(table))
        if table:
            self._width.addItem("Default", 0)
            for n, hz in enumerate(table, start=1):
                self._width.addItem(f"{hz} Hz", n)
        else:
            self._width.addItem("n/a in this mode", 0)
        self._width.blockSignals(False)

    # ------------------------------------------------------------------
    # From the user
    # ------------------------------------------------------------------

    def _on_width_activated(self, _row: int) -> None:
        self._touch("width")
        self.width_selected.emit(int(self._width.currentData() or 0))

    def _on_narrow_clicked(self, checked: bool) -> None:
        self._touch("narrow")
        self.narrow_toggled.emit(checked)

    def _on_shift_moved(self, value: int) -> None:
        self._touch("shift")
        self._shift_label.setText(self._fmt_shift(value * IF_SHIFT_STEP))
        self._send_timer.start()          # while dragging, send at most every 60 ms

    def _send_shift(self) -> None:
        self.shift_changed.emit(self._shift.value() * IF_SHIFT_STEP)

    # ------------------------------------------------------------------

    def _touch(self, name: str) -> None:
        self._touched[name] = time.monotonic()

    def _settled(self, name: str) -> bool:
        return time.monotonic() - self._touched.get(name, -HOLD_OFF_S) >= HOLD_OFF_S

    @staticmethod
    def _set_quiet(widget, setter) -> None:
        widget.blockSignals(True)
        setter(widget)
        widget.blockSignals(False)

    @staticmethod
    def _fmt_shift(hz: int) -> str:
        return f"{hz:+d} Hz" if hz else "0 Hz"
