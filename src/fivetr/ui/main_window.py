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
from ..keyer.sidetone import SidetoneGenerator
from ..keyer.winkeyer import WinKeyer
from ..remote.audio import AudioUDPClient
from ..remote.client import RemoteRadio
from ..remote.server import RemoteServer
from ..spectrum.scope_audio import AudioScopeSource
from ..vcat.server import VCATServer

from .cluster_panel import ClusterPanel
from .cw_keyboard import CWKeyboard
from .freq_display import FreqDisplay
from .keyer_widget import KeyerWidget
from .macro_widget import MacroWidget
from .log_panel import LogPanel
from .meter_widget import MeterWidget
from .mode_buttons import ModePanel
from .settings_dialog import SettingsDialog
from .waterfall_widget import WaterfallWidget
from ..cat.base import Meters, Mode, VFO
from ..keyer.keyer import CWKeyer

logger = logging.getLogger(__name__)

# CW element sequence per character
_CW_ELEMENTS: dict[str, str] = {
    'A': '.-',    'B': '-...',  'C': '-.-.',  'D': '-..',
    'E': '.',     'F': '..-.',  'G': '--.',   'H': '....',
    'I': '..',    'J': '.---',  'K': '-.-',   'L': '.-..',
    'M': '--',    'N': '-.',    'O': '---',   'P': '.--.',
    'Q': '--.-',  'R': '.-.',   'S': '...',   'T': '-',
    'U': '..-',   'V': '...-',  'W': '.--',   'X': '-..-',
    'Y': '-.--',  'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--',
    '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..', '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', "'": '.----.',
    '!': '-.-.--', '/': '-..-.',  '(': '-.--.',  ')': '-.--.-',
    '&': '.-...',  ':': '---...', ';': '-.-.-.', '=': '-...-',
    '+': '.-.-.',  '-': '-....-', '_': '..--.-', '"': '.-..-.',
    '$': '...-..-','@': '.--.-.',
}


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


def _generate_cw_events(text: str, wpm: int) -> list[tuple[bool, float]]:
    """Generate (key_down, duration_ms) events for CW macro sidetone simulation.

    Breaks *text* into dit/dah elements and calculates timing from *wpm*
    (dit unit = 1200 / wpm ms).  The remote client uses this to produce
    realistic CW sidetone and TX LED during macro playback when the shack
    WinKeyer's breakin bit isn't available.
    """
    unit = 1200.0 / wpm
    events: list[tuple[bool, float]] = []

    for i, ch in enumerate(text.upper()):
        if ch == ' ':
            if events:
                events.append((False, 6 * unit))
            continue

        elems = _CW_ELEMENTS.get(ch)
        if elems is None:
            continue

        for e in elems:
            events.append((True, unit if e == '.' else 3 * unit))
            events.append((False, unit))

        if i < len(text) - 1 and text[i + 1] != ' ':
            events.append((False, 2 * unit))

    return events


class MainWindow(QMainWindow):
    """5trig main application window."""

    # Internal signals for thread-safe UI updates
    _state_changed = Signal(object)          # RadioState
    _spot_received = Signal(object)          # DXSpot
    _connection_changed = Signal(bool)       # connected?
    _scope_data = Signal(object)             # np.ndarray — crosses audio→main thread
    _key_state = Signal(bool)                # key down/up — crosses WinKeyer thread→main thread

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("5trig — Yaesu Control")
        self.setMinimumSize(1200, 700)

        self._config = load_config()
        self._radio: CATRadio | RemoteRadio | None = None
        self._vcat: VCATServer | None = None
        self._audio_scope: AudioScopeSource | None = None
        self._remote_server: RemoteServer | None = None
        self._audio_udp_client: AudioUDPClient | None = None
        self._winkeyer: WinKeyer | None = None
        self._keyer: CWKeyer | None = None
        self._sidetone: SidetoneGenerator | None = None
        self._cluster: ClusterClient | None = None
        self._adif_log: ADIFLog | None = None
        self._udp_logger = UDPLogger()
        self._macro_task: asyncio.Task | None = None
        self._keyboard_cw_queue: asyncio.Queue[str] = asyncio.Queue()
        self._keyboard_cw_task: asyncio.Task | None = None
        self._keyboard_sideline_active = False

        self._setup_logging_backend()
        self._build_ui()
        self._connect_signals()
        self._apply_dark_theme()

        # State poll timer (updates UI from radio state periodically)
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(100)
        self._ui_timer.timeout.connect(self._on_ui_timer)

        # Auto-connect in remote client mode
        if self._config.remote.client_enabled:
            QTimer.singleShot(0, lambda: self._connect_btn.setChecked(True))

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

        tb.addSeparator()

        # Share button — shack side only; starts the remote server
        self._share_btn = QPushButton("📡 Share")
        self._share_btn.setCheckable(True)
        self._share_btn.setEnabled(False)   # enabled once radio is connected
        self._share_btn.setToolTip(
            "Share this radio over the network so a remote 5trig client can connect"
        )
        self._share_btn.setStyleSheet(
            "QPushButton:checked { background-color: #005580; color: white; font-weight: bold; }"
            "QPushButton { padding: 4px 12px; }"
        )
        self._share_btn.toggled.connect(self._on_share_toggle)
        tb.addWidget(self._share_btn)

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

        # CW Keyer bar
        self._keyer_widget = KeyerWidget()
        self._keyer_widget.settings_changed.connect(self._on_keyer_settings_changed)
        self._keyer_widget.apply_config(self._config.keyer)
        v_layout.addWidget(self._keyer_widget)

        # CW macro buttons (F1–F8)
        self._macro_widget = MacroWidget()
        self._macro_widget.macro_triggered.connect(self._on_macro_triggered)
        self._macro_widget.stop_triggered.connect(self._on_macro_stop)
        self._macro_widget.apply_macros(self._config.cw_macros, self._config.callsign)
        v_layout.addWidget(self._macro_widget)

        # CW keyboard (type-ahead)
        self._cw_keyboard = CWKeyboard()
        self._cw_keyboard.char_sent.connect(self._on_cw_char)
        self._cw_keyboard.stop_requested.connect(self._on_macro_stop)
        v_layout.addWidget(self._cw_keyboard)

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
        self._status_remote = QLabel("")       # shows share/client status
        self._status_cluster = QLabel("Cluster: off")
        self._status_call = QLabel(f"📻 {self._config.callsign or '(no callsign)'}")
        sb.addWidget(self._status_radio)
        sb.addPermanentWidget(self._status_remote)
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

        # CW Macros — F1–F8 shortcuts
        from PySide6.QtGui import QKeySequence
        macro_menu = mb.addMenu("&Macros")
        for i in range(8):
            act = QAction(f"&F{i + 1} Macro", self)
            act.setShortcut(QKeySequence(f"F{i + 1}"))
            act.triggered.connect(lambda _, idx=i: self._on_macro_triggered(idx))
            macro_menu.addAction(act)
        macro_menu.addSeparator()
        stop_act = QAction("&Stop CW", self)
        stop_act.setShortcut(QKeySequence("Escape"))
        stop_act.triggered.connect(self._on_macro_stop)
        macro_menu.addAction(stop_act)

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
        self._key_state.connect(self._on_key_state_ui)

    # ------------------------------------------------------------------
    # Radio connection
    # ------------------------------------------------------------------

    def _on_connect_toggle(self, checked: bool) -> None:
        if checked:
            asyncio.ensure_future(self._do_connect())
        else:
            asyncio.ensure_future(self._do_disconnect())

    async def _do_connect(self) -> None:
        rm_cfg = self._config.remote

        # ---- Remote client mode ----
        if rm_cfg.client_enabled:
            host = rm_cfg.server_host
            port = rm_cfg.server_port
            if not host:
                QMessageBox.critical(
                    self, "Remote Connection",
                    "Remote mode is enabled but no server host is configured.\n"
                    "Go to Settings → Remote and enter the shack machine's IP address."
                )
                self._connect_btn.setChecked(False)
                return

            self._radio = RemoteRadio(host, port)
            self._radio.add_listener(self._on_radio_event)
            try:
                await self._radio.connect()
            except Exception as e:
                logger.error("Remote connect failed: %s", e)
                QMessageBox.critical(
                    self, "Remote Connection Error",
                    f"Could not connect to {host}:{port}:\n{e}\n\n"
                    "Check that 5trig is running on the shack machine with Share enabled."
                )
                self._connect_btn.setChecked(False)
                return

            self._status_remote.setText(f"🛰 Remote: {host}:{port}")
            # Start UDP audio client if the server advertised an audio port
            if self._radio.audio_udp_port is not None:
                self._audio_udp_client = AudioUDPClient(
                    server_host=host,
                    server_udp_port=self._radio.audio_udp_port,
                    sample_rate=self._config.waterfall.sample_rate,
                )
                self._audio_udp_client.start()
                # Run local FFT on decoded audio for the waterfall
                self._start_remote_audio_scope()

            if self._config.keyer.enabled:
                await self._start_keyer()
            self._start_sidetone()
            self._ptt_btn.setEnabled(True)
            self._ui_timer.start()
            self._connection_changed.emit(True)
            return

        # ---- Local radio mode ----
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

        # Auto-start remote sharing if configured
        if rm_cfg.share_enabled:
            self._share_btn.setChecked(True)   # triggers _on_share_toggle

        # Open WinKeyer if a port is configured (independent of Share / keyer enabled)
        wk_port = self._config.keyer.winkeyer_port
        if wk_port and not self._winkeyer:
            try:
                self._winkeyer = WinKeyer(wk_port, speed=self._config.keyer.wpm, mode=self._config.keyer.mode)
                self._winkeyer.open()
            except Exception as e:
                logger.error("WinKeyer failed to open on %s: %s", wk_port, e)
                self._winkeyer = None

        # Start sidetone if enabled
        self._start_sidetone()

        # Start keyboard CW sidetone consumer
        self._keyboard_cw_task = asyncio.ensure_future(self._consume_keyboard_cw())

        # Start keyer if enabled
        if self._config.keyer.enabled:
            await self._start_keyer()

        # Auto-connect cluster
        if self._config.cluster.auto_connect and self._config.cluster.callsign:
            asyncio.ensure_future(self._start_cluster())

        self._share_btn.setEnabled(True)
        self._ptt_btn.setEnabled(True)
        self._ui_timer.start()
        self._connection_changed.emit(True)

    async def _do_disconnect(self) -> None:
        self._ui_timer.stop()

        # Stop keyer
        if self._keyer:
            await self._keyer.stop()
            self._keyer = None
            self._keyer_widget.set_key_active(False)

        # Stop remote audio client if running
        if self._audio_udp_client:
            self._audio_udp_client.stop()
            self._audio_udp_client = None

        # Stop sidetone
        if self._sidetone:
            self._sidetone.stop()
            self._sidetone = None

        # Cancel keyboard CW sidetone consumer
        if self._keyboard_cw_task and not self._keyboard_cw_task.done():
            self._keyboard_cw_task.cancel()
            self._keyboard_cw_task = None
        while not self._keyboard_cw_queue.empty():
            self._keyboard_cw_queue.get_nowait()

        # Close WinKeyer (opened at connect, independent of Share)
        if self._winkeyer:
            self._winkeyer.close()
            self._winkeyer = None

        # Stop remote server if it was running
        if self._remote_server:
            await self._remote_server.stop()
            self._remote_server = None
            self._share_btn.setChecked(False)
            self._status_remote.setText("")

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

        self._share_btn.setEnabled(False)
        self._ptt_btn.setEnabled(False)
        self._ptt_btn.setChecked(False)
        self._status_remote.setText("")
        self._connection_changed.emit(False)

    # ------------------------------------------------------------------
    # Share toggle (shack side — starts / stops RemoteServer)
    # ------------------------------------------------------------------

    def _on_share_toggle(self, checked: bool) -> None:
        if checked:
            asyncio.ensure_future(self._start_remote_server())
        else:
            asyncio.ensure_future(self._stop_remote_server())

    async def _start_remote_server(self) -> None:
        rm_cfg = self._config.remote
        audio_port = rm_cfg.audio_udp_port if rm_cfg.audio_stream else None
        self._remote_server = RemoteServer(
            self._radio,
            host=rm_cfg.share_host,
            port=rm_cfg.server_port,
            audio_port=audio_port,
            audio_bitrate=rm_cfg.audio_bitrate,
            sample_rate=self._config.waterfall.sample_rate,
            winkeyer=self._winkeyer,
            paddle_udp_port=rm_cfg.paddle_udp_port,
        )
        if self._audio_scope:
            # Forward FFT frames for the remote waterfall
            self._audio_scope.add_callback(self._remote_server.on_scope_data)
            # Forward raw PCM for Opus/UDP audio streaming
            if audio_port is not None:
                self._audio_scope.add_pcm_callback(self._remote_server.on_pcm)
        try:
            await self._remote_server.start()
            import socket
            local_ip = socket.gethostbyname(socket.gethostname())
            audio_info = f" + audio UDP:{audio_port}" if audio_port else ""
            self._status_remote.setText(
                f"📡 Sharing: {local_ip}:{rm_cfg.server_port}{audio_info}"
            )
            logger.info("Remote server started on port %d", rm_cfg.server_port)
        except OSError as e:
            logger.error("Remote server failed to start: %s", e)
            self._status_remote.setText(f"📡 Share failed: port {rm_cfg.server_port} in use")
            self._remote_server = None
            self._share_btn.setChecked(False)

    async def _stop_remote_server(self) -> None:
        if self._remote_server:
            if self._audio_scope:
                try:
                    self._audio_scope.remove_callback(self._remote_server.on_scope_data)
                except ValueError:
                    pass
                self._audio_scope.remove_pcm_callback(self._remote_server.on_pcm)
            await self._remote_server.stop()
            self._remote_server = None
        self._status_remote.setText("")

    # ------------------------------------------------------------------
    # Audio scope
    # ------------------------------------------------------------------

    def _start_audio_scope(self) -> None:
        wf_cfg = self._config.waterfall
        self._audio_scope = AudioScopeSource(
            device=wf_cfg.audio_device,
            pw_node=wf_cfg.pw_node,
            sample_rate=wf_cfg.sample_rate,
            fft_size=wf_cfg.fft_size,
        )
        self._audio_scope.add_callback(self._on_audio_fft)
        # Run in thread to avoid blocking Qt event loop
        import threading
        t = threading.Thread(target=self._audio_scope.start, daemon=True)
        t.start()

    def _start_remote_audio_scope(self) -> None:
        """Create an FFT processor fed by decoded remote audio for the waterfall."""
        wf_cfg = self._config.waterfall
        self._audio_scope = AudioScopeSource(
            sample_rate=wf_cfg.sample_rate,
            fft_size=wf_cfg.fft_size,
        )
        self._audio_scope.add_callback(self._on_audio_fft)
        self._audio_udp_client.add_pcm_callback(self._audio_scope._process_samples)

    def _on_audio_fft(self, db_array) -> None:
        """Called from audio thread — must NOT touch Qt directly.

        Emit a signal instead; Qt routes it to the main thread via
        QueuedConnection before push_line() runs.
        """
        self._scope_data.emit(db_array.copy())   # .copy() avoids race on buffer reuse

    # ------------------------------------------------------------------
    # CW Keyer
    # ------------------------------------------------------------------

    async def _start_keyer(self) -> None:
        """Create and start the CW keyer using current config + widget state."""
        # Sync config from widget before starting
        self._sync_keyer_config()
        self._keyer = CWKeyer(
            radio=self._radio,
            config=self._config.keyer,
            key_state_callback=self._on_key_state,
        )
        await self._keyer.start()

    def _sync_keyer_config(self) -> None:
        """Copy widget state → config (before starting keyer)."""
        cfg = self._config.keyer
        cfg.mode = self._keyer_widget.mode
        cfg.wpm = self._keyer_widget.wpm

    def _on_key_state(self, down: bool) -> None:
        """Called from asyncio loop when the key goes down/up.

        Updates the LED in the keyer widget and gates the sidetone.
        """
        self._keyer_widget.set_key_active(down)
        if self._sidetone and not (self._winkeyer and self._winkeyer.is_open):
            self._sidetone.key(down)

    def _on_key_state_ui(self, down: bool) -> None:
        """Slot for _key_state signal — runs on main thread from WinKeyer thread."""
        self._keyer_widget.set_key_active(down)

    def _on_keyer_settings_changed(self) -> None:
        """User changed mode or WPM in the keyer widget."""
        wpm = self._keyer_widget.wpm
        if self._winkeyer:
            self._winkeyer.set_speed(wpm)
        if self._keyer is None:
            return
        self._keyer.update_wpm(wpm)
        self._keyer.update_mode(self._keyer_widget.mode)

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
            # Forward state to any connected remote clients
            if self._remote_server:
                await self._remote_server.on_state_changed(payload)
        elif event == RadioEvent.SCOPE_DATA:
            # CAT-based scope (FTdx-10/101) — push to waterfall and remote clients
            self._scope_data.emit(payload)
            if self._remote_server:
                self._remote_server.on_scope_data(payload)

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
            if isinstance(self._radio, RemoteRadio):
                model = self._radio.state.model
                host = self._config.remote.server_host
                self._status_radio.setText(f"🟢 {model} @ {host}")
            else:
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
    # CW Macros
    # ------------------------------------------------------------------

    def _on_macro_triggered(self, idx: int) -> None:
        """Send CW macro slot *idx* via WinKeyer (local or remote)."""
        macros = self._config.cw_macros
        if idx >= len(macros):
            return
        text = macros[idx].text.strip()
        if not text:
            return
        text = text.replace("{CALL}", self._config.callsign or "")

        if isinstance(self._radio, RemoteRadio):
            self._radio.cw_send_text(text)
            self._macro_widget.set_active(True)
            # Simulate local dit/dah events for sidetone + LED
            # since the server's WinKeyer breakin bit doesn't fire
            # reliably during text playback.
            if self._config.keyer.sidetone or self._config.keyer.enabled:
                events = _generate_cw_events(text, self._config.keyer.wpm)
                self._macro_task = asyncio.ensure_future(
                    self._schedule_cw_events(events)
                )
        elif self._winkeyer and self._winkeyer.is_open:
            self._winkeyer.clear_buffer()
            self._winkeyer.send_text(text)
            self._macro_widget.set_active(True)

    def _on_macro_stop(self) -> None:
        """Abort current WinKeyer transmission."""
        if self._macro_task and not self._macro_task.done():
            self._macro_task.cancel()
            self._macro_task = None
        if self._keyboard_cw_task and not self._keyboard_cw_task.done():
            self._keyboard_cw_task.cancel()
        if isinstance(self._radio, RemoteRadio):
            self._radio.cw_stop()
        elif self._winkeyer and self._winkeyer.is_open:
            self._winkeyer.clear_buffer()
        self._macro_widget.set_active(False)
        if self._sidetone:
            self._sidetone.key(False)
        self._key_state.emit(False)
        # Clear queued keyboard chars so they don't play after stop
        while not self._keyboard_cw_queue.empty():
            self._keyboard_cw_queue.get_nowait()

    def _on_cw_char(self, ch: str) -> None:
        """Send a single character to the WinKeyer (local or remote)."""
        if isinstance(self._radio, RemoteRadio):
            self._radio.cw_send_char(ch)
        elif self._winkeyer and self._winkeyer.is_open:
            self._winkeyer.send_text(ch)

        if self._config.keyer.sidetone or self._config.keyer.enabled:
            self._keyboard_cw_queue.put_nowait(ch.upper())

    # ------------------------------------------------------------------
    # Sidetone
    # ------------------------------------------------------------------

    def _start_sidetone(self) -> None:
        """Start the software sidetone if enabled in config.

        WinKeyer key / busy callbacks are always registered so the
        TX indicator LED (and sidetone when enabled) work during
        macro playback, regardless of the sidetone config setting.
        """
        logger.info("_start_sidetone called (remote=%s)", isinstance(self._radio, RemoteRadio))
        if self._config.keyer.sidetone:
            self._sidetone = SidetoneGenerator(
                freq=self._config.keyer.sidetone_freq,
                volume=self._config.keyer.sidetone_volume,
            )
            self._sidetone.start()
        if self._winkeyer and self._winkeyer.is_open:
            self._winkeyer.set_key_callback(self._on_wk_key_event)
            self._winkeyer.set_busy_callback(self._on_wk_busy_event)
        elif isinstance(self._radio, RemoteRadio):
            self._radio.set_key_callback(self._on_wk_key_event)

    def _on_wk_key_event(self, down: bool) -> None:
        """Called from WinKeyer status thread on breakin change (key down/up)."""
        logger.info("WK key event: down=%s, sidetone=%s", down, self._sidetone is not None)
        if self._sidetone and not self._keyboard_sideline_active:
            self._sidetone.key(down)
        self._key_state.emit(down)

    def _on_wk_busy_event(self, busy: bool) -> None:
        """Called from WinKeyer status thread on buffer busy/idle."""
        logger.info("WK busy event: busy=%s, sidetone=%s", busy, self._sidetone is not None)
        if self._sidetone and not self._keyboard_sideline_active:
            self._sidetone.key(busy)
        self._key_state.emit(busy)

    async def _schedule_cw_events(self, events: list[tuple[bool, float]]) -> None:
        """Play key events with timing. Cancelled on macro stop."""
        for down, duration_ms in events:
            self._on_wk_key_event(down)
            await asyncio.sleep(duration_ms / 1000.0)

    async def _consume_keyboard_cw(self) -> None:
        """Play sidetone for keyboard CW characters sequentially."""
        unit = 1200.0 / self._config.keyer.wpm
        while True:
            ch = await self._keyboard_cw_queue.get()
            if ch not in _CW_ELEMENTS:
                continue
            self._keyboard_sideline_active = True
            try:
                events = _generate_cw_events(ch, self._config.keyer.wpm)
                await self._schedule_cw_events(events)
                # Inter-character gap: _generate_cw_events for a single char
                # leaves only 1 unit (element space). Add 2 more for 3-unit spacing.
                await asyncio.sleep(2 * unit / 1000.0)
            finally:
                self._keyboard_sideline_active = False

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
            self._macro_widget.apply_macros(
                self._config.cw_macros, self._config.callsign
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
