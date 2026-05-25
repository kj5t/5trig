"""
CW Keyer control panel.

A compact horizontal row placed below the S-meter.  Shows MIDI device
status, keyer mode, WPM, and a TX key-active indicator.

The widget is display-only beyond its controls — actual keyer start/stop
is managed by MainWindow.  Use apply_config() to pre-fill from settings
and read the properties (midi_port, mode, wpm) to build a KeyerConfig.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from ..keyer.midi_input import MidiInput

logger = logging.getLogger(__name__)


class KeyerWidget(QWidget):
    """Compact CW keyer control bar.

    Signals
    -------
    settings_changed:
        Emitted when the user changes MIDI port, mode, or WPM.
        MainWindow should restart the keyer with the new settings.
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

        # ---- MIDI device ----
        layout.addWidget(_label("MIDI:"))
        self._midi_combo = QComboBox()
        self._midi_combo.setMinimumWidth(180)
        self._midi_combo.setToolTip(
            "MIDI input device\n"
            "Vail adapter, WinKeyer USB (MIDI mode), or any USB MIDI keyer"
        )
        self._populate_midi_combo()
        self._midi_combo.currentIndexChanged.connect(self.settings_changed)
        layout.addWidget(self._midi_combo)

        # Refresh button
        refresh = QPushButton("⟳")
        refresh.setFixedWidth(28)
        refresh.setToolTip("Refresh MIDI device list")
        refresh.clicked.connect(self._on_refresh)
        layout.addWidget(refresh)

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

        # ---- MIDI status ----
        self._midi_status = QLabel("●  no MIDI")
        self._midi_status.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._midi_status)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_config(self, cfg) -> None:
        """Pre-fill controls from a KeyerConfig dataclass."""
        # Block signals during population to avoid spurious settings_changed
        self._midi_combo.blockSignals(True)
        self._mode_combo.blockSignals(True)
        self._wpm_spin.blockSignals(True)

        self._populate_midi_combo()

        if cfg.midi_port:
            idx = self._midi_combo.findData(cfg.midi_port)
            if idx >= 0:
                self._midi_combo.setCurrentIndex(idx)

        idx = self._mode_combo.findData(cfg.mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)

        self._wpm_spin.setValue(cfg.wpm)

        self._midi_combo.blockSignals(False)
        self._mode_combo.blockSignals(False)
        self._wpm_spin.blockSignals(False)

    def set_key_active(self, active: bool) -> None:
        """Called by MainWindow when the key goes down or up."""
        self._set_led(active)

    def set_midi_connected(self, ok: bool, port_name: str = "") -> None:
        """Update the MIDI status label."""
        if ok:
            short = port_name.split(":")[0][:22] if port_name else "connected"
            self._midi_status.setText(f"🟢 {short}")
            self._midi_status.setStyleSheet("color: #3FB950; font-size: 11px;")
        else:
            self._midi_status.setText("⚫ no MIDI")
            self._midi_status.setStyleSheet("color: #666; font-size: 11px;")

    # ------------------------------------------------------------------
    # Property getters (read these to build a KeyerConfig)
    # ------------------------------------------------------------------

    @property
    def midi_port(self) -> str:
        return self._midi_combo.currentData() or ""

    @property
    def mode(self) -> str:
        return self._mode_combo.currentData() or "iambic_a"

    @property
    def wpm(self) -> int:
        return self._wpm_spin.value()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        prev = self._midi_combo.currentData()
        self._populate_midi_combo()
        if prev:
            idx = self._midi_combo.findData(prev)
            if idx >= 0:
                self._midi_combo.setCurrentIndex(idx)

    def _populate_midi_combo(self) -> None:
        self._midi_combo.blockSignals(True)
        self._midi_combo.clear()
        self._midi_combo.addItem("(auto — first non-passthrough port)", "")
        for p in MidiInput.list_ports():
            self._midi_combo.addItem(p, p)
        self._midi_combo.blockSignals(False)

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
