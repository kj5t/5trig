"""
Main application window.

Wires together: CAT radio, virtual CAT server, waterfall, QSO log, DX cluster.
Uses qasync to bridge asyncio with the Qt event loop.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..cat import FT710, FTdx10, FTdx101, CATRadio, RadioEvent, RadioState
from ..cluster.client import ClusterClient
from ..cluster.spot import DXSpot
from ..config.settings import AppConfig, load_config, save_config
from ..logging.adif import ADIFLog
from ..logging.udp_log import UDPDestination, UDPLogger
from ..spectrum.scope_audio import AudioScopeSource
from ..vcat.server import VCATServer

from .cluster_panel import ClusterPanel
from .freq_display import FreqDisplay
from .log_panel import LogPanel
from .meter_widget import MeterWidget
from .mode_buttons import ModePanel
from .settings_dialog import SettingsDialog
from .waterfall_widget import WaterfallWidget
from ..cat.base import Meters, Mode, VFO

logger = logging.getLogger(__name__)


_RADIO_CLASSES: dict[str, type[CATRadio]] = {
    "FT-710": FT710,
    "FTdx-10": FTdx10,
    "FTdx-101": FTdx101,
}

_DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #0D1117;
    color: #C9D1D9;
}
QMenuBar { background-color: #161B22; }
QMenuBar::item:selected { background-color: #21262D; }
QMenu { background-color: #161B22; border: 1px solid #30363D; }
QMenu::item:selected { background-color: #21262D; }
QToolBar { background-color: #161B22; border-bottom: 1px solid #30363D; }
QDockWidget { background-color: #161B22; }
QDockWidget::title { background-color: #21262D; padding: 4px; }
QStatusBar { background-color: #161B22; }
QLineEdit, QComboBox, QSpinBox {
    background-color: #21262D;
    border: 1px solid #30363D;
    border-radius: 3px;
    padding: 2px 4px;
    color: #C9D1D9;
}
QGroupBox { border: 1px solid #30363D; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; padding: 0 4px; }
QTableWidget { background-color: #0D1117; gridline-color: #21262D; }
QTableWidget::item:selected { background-color: #0D419D; }
QHeaderView::section { background-color: #21262D; border: 1px solid #30363D; padding: 3px; }
QPushButton {
    background-color: #21262D;
    border: 1px solid #30363D;
    border-radius: 3px;
    padding: 4px 10px;
    color: #C9D1D9;
}
QPushButton:hover { background-color: #2D333B; }
QPushButton:pressed { background-color: #161B22; }
"""


class MainWindow(QMainWindow):
    """5trig main application window."""

    # Internal signals for thread-safe UI updates
    _state_changed = Signal(object)          # RadioState
    _spot_received = Signal(object)          # DXSpot
    _connection_changed = Signal(bool)       # connected?
    _scope_data = Signal(object)             # np.ndarray — crosses audio→main thread

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("5trig — Yaesu Control")
        self.setMinimumSize(1200, 700)

        self._config = load_config()
        self._radio: CATRadio | None = None
        self._vcat: VCATServer | None = None
        self._audio_scope: AudioScopeSource | None = None
        self._cluster: ClusterClient | None = None
        self._adif_log: ADIFLog | None = None
        self._udp_logger = UDPLogger()

        self._setup_logging_backend()
        self._build_ui()
        self._connect_signals()
        self._apply_dark_theme()

        # State poll timer (updates UI from radio state periodically)
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(100)
        self._ui_timer.timeout.connect(self._on_ui_timer)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_central_widget()
        self._build_dock_panels()
        self._build_status_bar()
        self._build_menu()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        self._connect_btn = QPushButton("⚡ Connect")
        self._connect_btn.setCheckable(True)
        self._connect_btn.setStyleSheet(
            "QPushButton:checked { background-color: #006600; color: white; font-weight: bold; }"
            "QPushButton { padding: 4px 12px; }"
        )
        self._connect_btn.toggled.connect(self._on_connect_toggle)
        tb.addWidget(self._connect_btn)

        tb.addSeparator()

        self._ptt_btn = QPushButton("🔴 PTT")
        self._ptt_btn.setCheckable(True)
        self._ptt_btn.setEnabled(False)
        self._ptt_btn.setStyleSheet(
            "QPushButton:checked { background-color: #CC0000; color: white; font-weight: bold; }"
            "QPushButton { padding: 4px 12px; }"
        )
        self._ptt_btn.toggled.connect(self._on_ptt_toggle)
        tb.addWidget(self._ptt_btn)

        tb.addSeparator()

        # Band quick-select buttons
        for band_mhz, label in [
            (1.840, "160"), (3.573, "80"), (7.074, "40"),
            (14.074, "20"), (21.074, "15"), (28.074, "10"),
        ]:
            btn = QPushButton(f"{label}m")
            btn.setMaximumWidth(48)
            hz = int(band_mhz * 1_000_000)
            btn.clicked.connect(lambda _, f=hz: self._tune_to(f))
            tb.addWidget(btn)

    def _build_central_widget(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        v_layout = QVBoxLayout(central)
        v_layout.setContentsMargins(4, 4, 4, 4)
        v_layout.setSpacing(4)

        # VFO row
        vfo_row = QHBoxLayout()

        # VFO A display
        self._vfo_a = FreqDisplay()
        self._vfo_a.set_vfo_label("VFO A")
        self._vfo_a.freq_entered.connect(self._on_freq_entered_a)

        # VFO B display
        self._vfo_b = FreqDisplay()
        self._vfo_b.set_vfo_label("VFO B")
        self._vfo_b.freq_entered.connect(self._on_freq_entered_b)

        # Swap button
        swap_btn = QPushButton("⇄")
        swap_btn.setToolTip("Swap VFOs")
        swap_btn.setMaximumWidth(30)
        swap_btn.clicked.connect(self._on_swap_vfos)

        vfo_row.addWidget(self._vfo_a, 3)
        vfo_row.addWidget(swap_btn)
        vfo_row.addWidget(self._vfo_b, 3)
        v_layout.addLayout(vfo_row)

        # Mode panel
        self._mode_panel = ModePanel()
        self._mode_panel.mode_selected.connect(self._on_mode_selected)
        v_layout.addWidget(self._mode_panel)

        # S-meter
        self._smeter = MeterWidget(Meters.SMETER)
        v_layout.addWidget(self._smeter)

        # Waterfall
        self._waterfall = WaterfallWidget(rows=512, cols=1024)
        self._waterfall.freq_clicked.connect(self._tune_to)
        v_layout.addWidget(self._waterfall, 1)

    def _build_dock_panels(self) -> None:
        # QSO Log dock
        log_dock = QDockWidget("QSO Log", self)
        log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._log_panel = LogPanel(self._adif_log or self._get_adif_log(), self._udp_logger)
        log_dock.setWidget(self._log_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

        # DX Cluster dock
        cluster_dock = QDockWidget("DX Cluster", self)
        cluster_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self._cluster_panel = ClusterPanel()
        self._cluster_panel.tune_to.connect(self._tune_to)
        cluster_dock.setWidget(self._cluster_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, cluster_dock)

    def _build_status_bar(self) -> None:
        sb: QStatusBar = self.statusBar()
        self._status_radio = QLabel("⚫ Disconnected")
        self._status_vcat = QLabel("vCAT: off")
        self._status_cluster = QLabel("Cluster: off")
        self._status_call = QLabel(f"📻 {self._config.callsign or '(no callsign)'}")
        sb.addWidget(self._status_radio)
        sb.addPermanentWidget(self._status_vcat)
        sb.addPermanentWidget(self._status_cluster)
        sb.addPermanentWidget(self._status_call)

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("&File")
        settings_act = QAction("&Settings…", self)
        settings_act.setShortcut("Ctrl+,")
        settings_act.triggered.connect(self._on_settings)
        file_menu.addAction(settings_act)
        file_menu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(QApplication.quit)
        file_menu.addAction(quit_act)

        # Radio
        radio_menu = mb.addMenu("&Radio")
        connect_act = QAction("&Connect / Disconnect", self)
        connect_act.triggered.connect(
            lambda: self._connect_btn.setChecked(not self._connect_btn.isChecked())
        )
        radio_menu.addAction(connect_act)

        # View
        view_menu = mb.addMenu("&View")
        # Dock toggles added later

        # Help
        help_menu = mb.addMenu("&Help")
        about_act = QAction("&About 5trig", self)
        about_act.triggered.connect(self._on_about)
        help_menu.addAction(about_act)

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._state_changed.connect(self._on_state_changed)
        self._spot_received.connect(self._cluster_panel.add_spot)
        self._connection_changed.connect(self._on_connection_changed)
        # Audio thread → main thread: QueuedConnection happens automatically
        # because _waterfall lives on the main thread.
        self._scope_data.connect(self._waterfall.push_line)

    # ------------------------------------------------------------------
    # Radio connection
    # ------------------------------------------------------------------

    def _on_connect_toggle(self, checked: bool) -> None:
        if checked:
            asyncio.ensure_future(self._do_connect())
        else:
            asyncio.ensure_future(self._do_disconnect())

    async def _do_connect(self) -> None:
        model_name = self._config.radio.model
        port = self._config.radio.port
        cls = _RADIO_CLASSES.get(model_name, FT710)

        self._radio = cls()
        self._radio.add_listener(self._on_radio_event)

        try:
            await self._radio.connect(port)
        except Exception as e:
            logger.error("Failed to connect: %s", e)
            QMessageBox.critical(
                self, "Connection Error",
                f"Could not open {port}:\n{e}\n\n"
                "Check radio is on, USB cable connected, and port correct in Settings."
            )
            self._connect_btn.setChecked(False)
            return

        # Start virtual CAT
        if self._config.vcat.enabled:
            self._vcat = VCATServer(
                self._radio,
                host=self._config.vcat.host,
                port=self._config.vcat.port,
            )
            try:
                await self._vcat.start()
                self._status_vcat.setText(
                    f"vCAT: {self._config.vcat.host}:{self._config.vcat.port}"
                )
            except OSError as e:
                logger.warning("vCAT server failed to start: %s", e)
                self._status_vcat.setText(f"vCAT: port {self._config.vcat.port} in use")

        # Start audio scope if configured
        if self._config.waterfall.source == "audio":
            self._start_audio_scope()

        # Auto-connect cluster
        if self._config.cluster.auto_connect and self._config.cluster.callsign:
            asyncio.ensure_future(self._start_cluster())

        self._ptt_btn.setEnabled(True)
        self._ui_timer.start()
        self._connection_changed.emit(True)

    async def _do_disconnect(self) -> None:
        self._ui_timer.stop()

        if self._vcat:
            await self._vcat.stop()
            self._vcat = None
            self._status_vcat.setText("vCAT: off")

        if self._cluster:
            await self._cluster.stop()
            self._cluster = None

        if self._audio_scope:
            self._audio_scope.stop()
            self._audio_scope = None

        if self._radio:
            await self._radio.disconnect()
            self._radio = None

        self._ptt_btn.setEnabled(False)
        self._ptt_btn.setChecked(False)
        self._connection_changed.emit(False)

    # ------------------------------------------------------------------
    # Audio scope
    # ------------------------------------------------------------------

    def _start_audio_scope(self) -> None:
        wf_cfg = self._config.waterfall
        self._audio_scope = AudioScopeSource(
            device=wf_cfg.audio_device,
            sample_rate=wf_cfg.sample_rate,
            fft_size=wf_cfg.fft_size,
        )
        self._audio_scope.add_callback(self._on_audio_fft)
        # Run in thread to avoid blocking Qt event loop
        import threading
        t = threading.Thread(target=self._audio_scope.start, daemon=True)
        t.start()

    def _on_audio_fft(self, db_array) -> None:
        """Called from sounddevice audio thread — must NOT touch Qt directly.

        Emit a signal instead; Qt routes it to the main thread via
        QueuedConnection before push_line() runs.
        """
        self._scope_data.emit(db_array.copy())   # .copy() avoids race on buffer reuse

    # ------------------------------------------------------------------
    # Cluster
    # ------------------------------------------------------------------

    async def _start_cluster(self) -> None:
        cfg = self._config.cluster
        self._cluster = ClusterClient(cfg.host, cfg.port, cfg.callsign)
        self._cluster.add_spot_callback(
            lambda spot: self._spot_received.emit(spot)
        )
        await self._cluster.start()
        self._status_cluster.setText(f"Cluster: {cfg.host}")

    # ------------------------------------------------------------------
    # Radio event listener (asyncio coroutine)
    # ------------------------------------------------------------------

    async def _on_radio_event(self, event: RadioEvent, payload) -> None:
        if event == RadioEvent.STATE_CHANGED:
            self._state_changed.emit(payload)
        elif event == RadioEvent.SCOPE_DATA:
            self._waterfall.push_line(payload)

    # ------------------------------------------------------------------
    # Qt Slots
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_state_changed(self, state: RadioState) -> None:
        self._vfo_a.set_freq(state.vfo_a_hz)
        self._vfo_b.set_freq(state.vfo_b_hz)
        self._mode_panel.set_mode(state.mode)
        smeter = state.meter_values.get(Meters.SMETER, 0.0)
        self._smeter.set_value(smeter)
        self._log_panel.update_radio_state(state)
        # Waterfall centre freq
        self._waterfall.set_centre(state.vfo_a_hz)

    @Slot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        if connected:
            model = self._config.radio.model
            port = self._config.radio.port
            self._status_radio.setText(f"🟢 {model} @ {port}")
            self._connect_btn.setText("⚡ Disconnect")
        else:
            self._status_radio.setText("⚫ Disconnected")
            self._connect_btn.setText("⚡ Connect")

    def _on_ui_timer(self) -> None:
        """Periodic UI refresh — request fresh state from radio."""
        if self._radio and self._radio.state.connected:
            self._radio.get_freq(VFO.A)
            self._radio.get_freq(VFO.B)

    # ------------------------------------------------------------------
    # VFO / Mode / PTT
    # ------------------------------------------------------------------

    def _on_freq_entered_a(self, hz: int) -> None:
        if self._radio:
            self._radio.set_freq(hz, VFO.A)

    def _on_freq_entered_b(self, hz: int) -> None:
        if self._radio:
            self._radio.set_freq(hz, VFO.B)

    def _on_mode_selected(self, mode: Mode) -> None:
        if self._radio:
            self._radio.set_mode(mode)

    def _on_ptt_toggle(self, checked: bool) -> None:
        if self._radio:
            self._radio.set_ptt(checked)

    def _on_swap_vfos(self) -> None:
        if self._radio:
            self._radio.enqueue(
                __import__("fivetr.cat.base", fromlist=["CATCommand"]).CATCommand(
                    raw=b"SV;", reply_len=0
                )
            )

    def _tune_to(self, hz: float) -> None:
        """Tune VFO-A to hz (from waterfall click or cluster spot)."""
        if self._radio:
            self._radio.set_freq(int(hz), VFO.A)
        self._vfo_a.set_freq(int(hz))

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            self._config = dlg.updated_config()
            save_config(self._config)
            self._status_call.setText(
                f"📻 {self._config.callsign or '(no callsign)'}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_adif_log(self) -> ADIFLog:
        if self._adif_log is None:
            log_dir = Path(self._config.logging.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            self._adif_log = ADIFLog(log_dir / self._config.logging.adif_file)
        return self._adif_log

    def _setup_logging_backend(self) -> None:
        self._adif_log = self._get_adif_log()
        if self._config.logging.udp_enabled:
            self._udp_logger.add_destination(
                UDPDestination(
                    host=self._config.logging.udp_host,
                    port=self._config.logging.udp_port,
                )
            )

    def _apply_dark_theme(self) -> None:
        QApplication.instance().setStyleSheet(_DARK_STYLE)  # type: ignore[union-attr]

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About 5trig",
            "<h2>5trig</h2>"
            "<p>Cross-platform Yaesu transceiver control</p>"
            "<p>Supports: FT-710 · FTdx-10 · FTdx-101</p>"
            "<p>Python + PySide6</p>",
        )

    def closeEvent(self, event) -> None:
        asyncio.ensure_future(self._do_disconnect())
        save_config(self._config)
        self._udp_logger.close()
        event.accept()
