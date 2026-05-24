"""Settings / preferences dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import AppConfig
from ..spectrum.scope_audio import AudioScopeSource


class SettingsDialog(QDialog):
    """Multi-tab settings dialog."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("5trig Settings")
        self.setMinimumWidth(480)
        self._config = config

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        tabs.addTab(self._build_station_tab(), "Station")
        tabs.addTab(self._build_radio_tab(), "Radio")
        tabs.addTab(self._build_vcat_tab(), "Virtual CAT")
        tabs.addTab(self._build_waterfall_tab(), "Waterfall")
        tabs.addTab(self._build_logging_tab(), "Logging")
        tabs.addTab(self._build_cluster_tab(), "DX Cluster")
        tabs.addTab(self._build_remote_tab(), "Remote")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_station_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config

        self._callsign = QLineEdit(cfg.callsign)
        self._callsign.textChanged.connect(lambda t: self._callsign.setText(t.upper()))
        self._grid = QLineEdit(cfg.grid_locator)
        self._country = QLineEdit(cfg.country)

        form.addRow("Callsign:", self._callsign)
        form.addRow("Grid Locator:", self._grid)
        form.addRow("Country:", self._country)
        return w

    def _build_radio_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config.radio

        self._radio_model = QComboBox()
        self._radio_model.addItems(["FT-710", "FTdx-10", "FTdx-101"])
        idx = self._radio_model.findText(cfg.model)
        if idx >= 0:
            self._radio_model.setCurrentIndex(idx)

        self._port = QLineEdit(cfg.port)
        self._baud = QComboBox()
        for b in [4800, 9600, 19200, 38400, 57600, 115200]:
            self._baud.addItem(str(b), b)
        idx = self._baud.findData(cfg.baud)
        if idx >= 0:
            self._baud.setCurrentIndex(idx)

        self._stopbits = QComboBox()
        self._stopbits.addItems(["1", "2"])
        self._stopbits.setCurrentText(str(cfg.stopbits))

        form.addRow("Radio Model:", self._radio_model)
        form.addRow("Serial Port:", self._port)
        form.addRow("Baud Rate:", self._baud)
        form.addRow("Stop Bits:", self._stopbits)
        return w

    def _build_vcat_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config.vcat

        self._vcat_enabled = QCheckBox("Enable Virtual CAT Server")
        self._vcat_enabled.setChecked(cfg.enabled)
        self._vcat_host = QLineEdit(cfg.host)
        self._vcat_port = QSpinBox()
        self._vcat_port.setRange(1024, 65535)
        self._vcat_port.setValue(cfg.port)

        form.addRow(self._vcat_enabled)
        form.addRow("Host:", self._vcat_host)
        form.addRow("Port:", self._vcat_port)
        form.addRow(QLabel("Point WSJT-X / JS8Call to this host:port\nusing Kenwood TS-2000 rig type."))
        return w

    def _build_waterfall_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config.waterfall

        self._wf_source = QComboBox()
        self._wf_source.addItems(["audio", "cat"])
        self._wf_source.setCurrentText(cfg.source)

        self._audio_dev = QComboBox()
        devices = AudioScopeSource.list_devices()
        self._audio_dev.addItem("(system default)", None)
        for d in devices:
            self._audio_dev.addItem(f"[{d['index']}] {d['name']}", d["index"])
        # Select saved device
        if cfg.audio_device is not None:
            idx = self._audio_dev.findData(cfg.audio_device)
            if idx >= 0:
                self._audio_dev.setCurrentIndex(idx)

        self._db_min = QSpinBox()
        self._db_min.setRange(-160, -40)
        self._db_min.setValue(int(cfg.db_min))
        self._db_max = QSpinBox()
        self._db_max.setRange(-120, 0)
        self._db_max.setValue(int(cfg.db_max))

        self._colormap = QComboBox()
        self._colormap.addItems(["plasma", "inferno", "turbo", "grayscale"])
        self._colormap.setCurrentText(cfg.color_map)

        form.addRow("Data Source:", self._wf_source)
        form.addRow("Audio Device:", self._audio_dev)
        form.addRow("Min dB:", self._db_min)
        form.addRow("Max dB:", self._db_max)
        form.addRow("Colour Map:", self._colormap)
        return w

    def _build_logging_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config.logging

        self._log_dir = QLineEdit(cfg.log_dir)
        self._adif_file = QLineEdit(cfg.adif_file)
        self._udp_enabled = QCheckBox("Broadcast QSOs via UDP")
        self._udp_enabled.setChecked(cfg.udp_enabled)
        self._udp_host = QLineEdit(cfg.udp_host)
        self._udp_port = QSpinBox()
        self._udp_port.setRange(1, 65535)
        self._udp_port.setValue(cfg.udp_port)
        self._lotw_user = QLineEdit(cfg.lotw_user)
        self._qrz_user = QLineEdit(cfg.qrz_user)

        form.addRow("Log Directory:", self._log_dir)
        form.addRow("ADIF File:", self._adif_file)
        form.addRow(self._udp_enabled)
        form.addRow("UDP Host:", self._udp_host)
        form.addRow("UDP Port:", self._udp_port)
        form.addRow("LoTW Username:", self._lotw_user)
        form.addRow("QRZ Username:", self._qrz_user)
        return w

    def _build_remote_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        cfg = self._config.remote

        # ---- Client mode (connect TO a remote shack server) ----
        client_box = QGroupBox("Client Mode — connect to a remote shack server")
        client_form = QFormLayout(client_box)

        self._remote_client_enabled = QCheckBox(
            "Use remote mode (connect to shack server instead of local serial port)"
        )
        self._remote_client_enabled.setChecked(cfg.client_enabled)

        self._remote_server_host = QLineEdit(cfg.server_host)
        self._remote_server_host.setPlaceholderText("e.g. 192.168.1.10 or shack.local")

        self._remote_server_port = QSpinBox()
        self._remote_server_port.setRange(1024, 65535)
        self._remote_server_port.setValue(cfg.server_port)

        client_form.addRow(self._remote_client_enabled)
        client_form.addRow("Shack server IP / host:", self._remote_server_host)
        client_form.addRow("Port:", self._remote_server_port)
        client_form.addRow(QLabel(
            "When enabled, the Radio tab's model / port settings are ignored.\n"
            "The shack machine must be running 5trig with Share 📡 active."
        ))
        layout.addWidget(client_box)

        # ---- Server / Share mode (shack side) ----
        server_box = QGroupBox("Server Mode — share this radio with remote clients")
        server_form = QFormLayout(server_box)

        self._remote_share_enabled = QCheckBox(
            "Auto-start sharing when radio connects (equivalent to clicking Share 📡)"
        )
        self._remote_share_enabled.setChecked(cfg.share_enabled)

        self._remote_share_host = QLineEdit(cfg.share_host)
        self._remote_share_host.setPlaceholderText("0.0.0.0 = all interfaces")

        server_form.addRow(self._remote_share_enabled)
        server_form.addRow("Bind address:", self._remote_share_host)
        server_form.addRow(QLabel(
            "Port (shared with client setting above).\n"
            "Use 0.0.0.0 to accept connections on all network interfaces.\n"
            "Restrict to 127.0.0.1 for loopback / SSH tunnel only.\n\n"
            "CW: remote clients can send PTT/key events; set radio to CW mode.\n"
            "WinKeyer / Vail adapter: use the cw_key protocol message from the\n"
            "remote client — see docs for MIDI-to-key integration."
        ))
        layout.addWidget(server_box)
        layout.addStretch()
        return w

    def _build_cluster_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        cfg = self._config.cluster

        self._cluster_host = QLineEdit(cfg.host)
        self._cluster_port = QSpinBox()
        self._cluster_port.setRange(1, 65535)
        self._cluster_port.setValue(cfg.port)
        self._cluster_call = QLineEdit(cfg.callsign)
        self._cluster_call.textChanged.connect(
            lambda t: self._cluster_call.setText(t.upper())
        )
        self._cluster_auto = QCheckBox("Auto-connect on startup")
        self._cluster_auto.setChecked(cfg.auto_connect)

        form.addRow("Cluster Host:", self._cluster_host)
        form.addRow("Port:", self._cluster_port)
        form.addRow("Login Callsign:", self._cluster_call)
        form.addRow(self._cluster_auto)
        return w

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        cfg = self._config
        cfg.callsign = self._callsign.text().strip()
        cfg.grid_locator = self._grid.text().strip()
        cfg.country = self._country.text().strip()

        cfg.radio.model = self._radio_model.currentText()
        cfg.radio.port = self._port.text().strip()
        cfg.radio.baud = self._baud.currentData()
        cfg.radio.stopbits = int(self._stopbits.currentText())

        cfg.vcat.enabled = self._vcat_enabled.isChecked()
        cfg.vcat.host = self._vcat_host.text().strip()
        cfg.vcat.port = self._vcat_port.value()

        cfg.waterfall.source = self._wf_source.currentText()
        cfg.waterfall.audio_device = self._audio_dev.currentData()
        cfg.waterfall.db_min = float(self._db_min.value())
        cfg.waterfall.db_max = float(self._db_max.value())
        cfg.waterfall.color_map = self._colormap.currentText()

        cfg.logging.log_dir = self._log_dir.text().strip()
        cfg.logging.adif_file = self._adif_file.text().strip()
        cfg.logging.udp_enabled = self._udp_enabled.isChecked()
        cfg.logging.udp_host = self._udp_host.text().strip()
        cfg.logging.udp_port = self._udp_port.value()
        cfg.logging.lotw_user = self._lotw_user.text().strip()
        cfg.logging.qrz_user = self._qrz_user.text().strip()

        cfg.cluster.host = self._cluster_host.text().strip()
        cfg.cluster.port = self._cluster_port.value()
        cfg.cluster.callsign = self._cluster_call.text().strip()
        cfg.cluster.auto_connect = self._cluster_auto.isChecked()

        cfg.remote.client_enabled = self._remote_client_enabled.isChecked()
        cfg.remote.server_host = self._remote_server_host.text().strip()
        cfg.remote.server_port = self._remote_server_port.value()
        cfg.remote.share_enabled = self._remote_share_enabled.isChecked()
        cfg.remote.share_host = self._remote_share_host.text().strip()

        self.accept()

    def updated_config(self) -> AppConfig:
        return self._config
