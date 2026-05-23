"""
DX cluster spots panel.

Displays incoming DX spots in a table.  Clicking a row tunes the radio to
that frequency (emits ``tune_to(float)`` with Hz).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..cluster.spot import DXSpot

_MAX_SPOTS = 200


class ClusterPanel(QWidget):
    """DX spot table with band/mode filter and click-to-tune."""

    tune_to = Signal(float)   # Hz

    _COLUMNS = ["Time", "DX Call", "Freq (MHz)", "Band", "Mode", "Spotter", "Comment"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spots: list[DXSpot] = []
        self._band_filter: str = ""
        self._mode_filter: str = ""

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_filter_bar())
        layout.addWidget(self._build_table())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_spot(self, spot: DXSpot) -> None:
        """Append a spot (call from cluster client callback → Qt signal bridge)."""
        self._spots.insert(0, spot)
        if len(self._spots) > _MAX_SPOTS:
            self._spots.pop()

        if self._matches_filter(spot):
            self._insert_row(spot, 0)
            if self._table.rowCount() > _MAX_SPOTS:
                self._table.removeRow(self._table.rowCount() - 1)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _matches_filter(self, spot: DXSpot) -> bool:
        if self._band_filter and spot.band.lower() != self._band_filter.lower():
            return False
        if self._mode_filter and spot.mode.upper() != self._mode_filter.upper():
            return False
        return True

    def _apply_filter(self) -> None:
        self._table.setRowCount(0)
        for spot in self._spots:
            if self._matches_filter(spot):
                self._insert_row(spot, self._table.rowCount())
                if self._table.rowCount() >= _MAX_SPOTS:
                    break

    # ------------------------------------------------------------------
    # UI builders
    # ------------------------------------------------------------------

    def _build_filter_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Band:"))
        self._band_edit = QLineEdit()
        self._band_edit.setPlaceholderText("20m")
        self._band_edit.setMaximumWidth(60)
        self._band_edit.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._band_edit)

        layout.addWidget(QLabel("Mode:"))
        self._mode_edit = QLineEdit()
        self._mode_edit.setPlaceholderText("FT8")
        self._mode_edit.setMaximumWidth(60)
        self._mode_edit.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._mode_edit)

        clear_btn = QPushButton("Clear Filter")
        clear_btn.clicked.connect(self._on_clear_filter)
        layout.addWidget(clear_btn)
        layout.addStretch()
        return layout

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        return self._table

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_filter_changed(self) -> None:
        self._band_filter = self._band_edit.text().strip()
        self._mode_filter = self._mode_edit.text().strip()
        self._apply_filter()

    def _on_clear_filter(self) -> None:
        self._band_edit.clear()
        self._mode_edit.clear()

    def _on_row_double_clicked(self, index) -> None:
        row = index.row()
        freq_item = self._table.item(row, 2)
        if freq_item:
            try:
                mhz = float(freq_item.text())
                self.tune_to.emit(mhz * 1_000_000.0)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _insert_row(self, spot: DXSpot, at_row: int) -> None:
        self._table.insertRow(at_row)
        cells = [
            spot.time_utc,
            spot.dx_call,
            f"{spot.freq_mhz:.3f}",
            spot.band,
            spot.mode,
            spot.spotter,
            spot.comment,
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if spot.mode == "FT8":
                item.setForeground(Qt.GlobalColor.cyan)
            elif spot.mode == "CW":
                item.setForeground(Qt.GlobalColor.yellow)
            self._table.setItem(at_row, col, item)
