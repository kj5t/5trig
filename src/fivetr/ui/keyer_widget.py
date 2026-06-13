"""
CW Keyer control panel.

A compact horizontal row placed below the S-meter.  Shows keyer mode, WPM,
and a TX key-active indicator.

The widget is display-only beyond its controls — actual keyer start/stop
is managed by MainWindow.  Use apply_config() to pre-fill from settings
and read the properties (mode, wpm) to build a KeyerConfig.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

logger = logging.getLogger(__name__)


class KeyerWidget(QWidget):
    """Compact CW keyer control bar.

    Signals
    -------
    settings_changed:
        Emitted when the user changes mode or WPM.
        MainWindow should update the keyer with the new settings.
    """

    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        # Section label
        lbl_cw = QLabel("CW Keyer:")
        lbl_cw.setStyleSheet("color: #8B949E; font-size: 11px;")
        layout.addWidget(lbl_cw)

        # ---- Keyer mode ----
        layout.addWidget(_label("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Iambic A", "iambic_a")
        self._mode_combo.addItem("Iambic B", "iambic_b")
        self._mode_combo.addItem("Straight", "straight")
        self._mode_combo.setToolTip(
            "Iambic A: squeeze release ends after current element\n"
            "Iambic B: one extra element inserted on squeeze release\n"
            "Straight: paddle events passed through directly (no timing)"
        )
        self._mode_combo.currentIndexChanged.connect(self.settings_changed)
        layout.addWidget(self._mode_combo)

        # ---- WPM ----
        layout.addWidget(_label("WPM:"))
        self._wpm_spin = QSpinBox()
        self._wpm_spin.setRange(5, 60)
        self._wpm_spin.setValue(20)
        self._wpm_spin.setFixedWidth(54)
        self._wpm_spin.setToolTip("Sending speed in words per minute")
        self._wpm_spin.valueChanged.connect(self.settings_changed)
        layout.addWidget(self._wpm_spin)

        # ---- TX indicator ----
        self._key_led = QLabel("⬤")
        self._key_led.setFixedWidth(16)
        self._key_led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_led.setToolTip("Key active (TX)")
        self._set_led(False)
        layout.addWidget(self._key_led)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_config(self, cfg) -> None:
        """Pre-fill controls from a KeyerConfig dataclass."""
        self._mode_combo.blockSignals(True)
        self._wpm_spin.blockSignals(True)

        idx = self._mode_combo.findData(cfg.mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)

        self._wpm_spin.setValue(cfg.wpm)

        self._mode_combo.blockSignals(False)
        self._wpm_spin.blockSignals(False)

    def set_key_active(self, active: bool) -> None:
        """Called by MainWindow when the key goes down or up."""
        self._set_led(active)

    # ------------------------------------------------------------------
    # Property getters (read these to build a KeyerConfig)
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode_combo.currentData() or "iambic_a"

    @property
    def wpm(self) -> int:
        return self._wpm_spin.value()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _set_led(self, active: bool) -> None:
        if active:
            self._key_led.setStyleSheet(
                "color: #FF4444; font-size: 14px; font-weight: bold;"
            )
        else:
            self._key_led.setStyleSheet("color: #2D333B; font-size: 14px;")


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #8B949E; font-size: 11px;")
    return lbl
