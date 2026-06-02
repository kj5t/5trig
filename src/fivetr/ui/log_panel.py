"""
QSO log panel.

A split view with:
  - Top: quick-entry form (callsign, RST, name, band auto-filled from VFO)
  - Bottom: scrollable log table with ADIF export button
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..cat.base import Band, RadioState
from ..logging.adif import ADIFLog, QSORecord
from ..logging.udp_log import UDPLogger


class LogPanel(QWidget):
    """QSO entry and log table widget."""

    qso_logged = Signal(object)  # QSORecord

    _COLUMNS = ["UTC", "Callsign", "Freq (MHz)", "Mode", "RST Sent", "RST Rcvd", "Name", "Notes"]

    def __init__(
        self,
        adif_log: ADIFLog,
        udp_logger: UDPLogger | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._log = adif_log
        self._udp = udp_logger
        self._current_freq_khz: float = 14074.0
        self._current_mode: str = "USB"
        self._populating = False

        layout = QVBoxLayout(self)

        # Entry form
        layout.addWidget(self._build_entry_form())

        # Log table
        layout.addWidget(self._build_table())

        # Toolbar
        layout.addLayout(self._build_toolbar())

        # Populate existing records
        self._populate_table()

    # ------------------------------------------------------------------
    # State sync from radio
    # ------------------------------------------------------------------

    def update_radio_state(self, state: RadioState) -> None:
        self._current_freq_khz = state.vfo_a_hz / 1000.0
        self._current_mode = state.mode.name
        # Auto-fill band
        band = Band.from_hz(state.vfo_a_hz)
        if band != Band.UNKNOWN:
            self._band_edit.setText(f"{band.value}m")

    # ------------------------------------------------------------------
    # UI builders
    # ------------------------------------------------------------------

    def _build_entry_form(self) -> QGroupBox:
        box = QGroupBox("Log QSO")
        layout = QHBoxLayout(box)

        self._call_edit = QLineEdit()
        self._call_edit.setPlaceholderText("Callsign")
        self._call_edit.setMaximumWidth(120)
        self._call_edit.textChanged.connect(lambda t: self._call_edit.setText(t.upper()))

        self._rst_s_edit = QLineEdit("599")
        self._rst_s_edit.setMaximumWidth(50)

        self._rst_r_edit = QLineEdit("599")
        self._rst_r_edit.setMaximumWidth(50)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Name")
        self._name_edit.setMaximumWidth(100)

        self._band_edit = QLineEdit("20m")
        self._band_edit.setMaximumWidth(50)

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("Notes")

        log_btn = QPushButton("Log QSO")
        log_btn.setDefault(True)
        log_btn.clicked.connect(self._on_log)
        log_btn.setStyleSheet(
            "QPushButton { background-color: #006600; color: white; "
            "font-weight: bold; padding: 4px 12px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #008800; }"
        )

        for label, widget in [
            ("Call:", self._call_edit),
            ("RST↑:", self._rst_s_edit),
            ("RST↓:", self._rst_r_edit),
            ("Name:", self._name_edit),
            ("Band:", self._band_edit),
            ("Notes:", self._notes_edit),
        ]:
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)

        layout.addWidget(log_btn)
        return box

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setSortingEnabled(True)
        self._table.cellChanged.connect(self._on_cell_changed)
        return self._table

    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        export_btn = QPushButton("Export ADIF…")
        export_btn.clicked.connect(self._on_export)
        layout.addWidget(export_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.setStyleSheet(
            "QPushButton { color: #CC4444; }"
            "QPushButton:hover { background-color: #2D1010; }"
        )
        delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(delete_btn)

        layout.addStretch()
        return layout

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_log(self) -> None:
        call = self._call_edit.text().strip().upper()
        if not call:
            self._call_edit.setFocus()
            return

        now = datetime.now(tz=timezone.utc)
        qso = QSORecord(
            call=call,
            freq_khz=self._current_freq_khz,
            band=self._band_edit.text().strip(),
            mode=self._current_mode,
            rst_sent=self._rst_s_edit.text().strip(),
            rst_rcvd=self._rst_r_edit.text().strip(),
            name=self._name_edit.text().strip(),
            notes=self._notes_edit.text().strip(),
            qso_date=now.strftime("%Y%m%d"),
            time_on=now.strftime("%H%M%S"),
        )

        self._log.add(qso)
        self._add_row(qso)

        if self._udp:
            self._udp.log_qso(qso)

        self.qso_logged.emit(qso)

        # Reset
        self._call_edit.clear()
        self._notes_edit.clear()
        self._rst_s_edit.setText("599")
        self._rst_r_edit.setText("599")
        self._name_edit.clear()
        self._call_edit.setFocus()

    def _on_export(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export ADIF",
            str(Path.home() / "qso_export.adi"),
            "ADIF Files (*.adi *.adif)",
        )
        if dest:
            self._log.export_adif(Path(dest))

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _populate_table(self) -> None:
        self._populating = True
        for qso in self._log.records:
            self._add_row(qso)
        self._populating = False

    def _add_row(self, qso: QSORecord) -> None:
        was_populating = self._populating
        self._populating = True
        row = self._table.rowCount()
        self._table.insertRow(row)
        cells = [
            f"{qso.qso_date[:4]}-{qso.qso_date[4:6]}-{qso.qso_date[6:]} {qso.time_on[:2]}:{qso.time_on[2:4]}Z",
            qso.call,
            f"{qso.freq_khz / 1000:.6f}",
            qso.mode,
            qso.rst_sent,
            qso.rst_rcvd,
            qso.name,
            qso.notes,
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()
        self._populating = was_populating

    _COL_ATTRS = ["_utc", "call", "freq_khz", "mode", "rst_sent", "rst_rcvd", "name", "notes"]

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._populating:
            return
        if row >= len(self._log.records):
            return
        qso = self._log.records[row]
        value = self._table.item(row, col).text().strip()
        attr = self._COL_ATTRS[col]
        if attr == "_utc":
            return
        if attr == "freq_khz":
            try:
                qso.freq_khz = float(value) * 1000
            except ValueError:
                return
        else:
            setattr(qso, attr, value)
        self._log.update(row, qso)

    def _on_delete(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        n = len(rows)
        reply = QMessageBox.question(
            self,
            "Delete QSO",
            f"Delete {n} selected QSO{'s' if n > 1 else ''}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._populating = True
        for row in rows:
            if row < len(self._log.records):
                self._log.delete(row)
            self._table.removeRow(row)
        self._populating = False
