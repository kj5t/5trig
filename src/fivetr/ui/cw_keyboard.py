"""CW keyboard — type-ahead text input that sends Morse via WinKeyer."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget


class _KeyboardInput(QLineEdit):
    """QLineEdit that emits each printable character as it's typed."""

    char_typed = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            return
        text = event.text()
        if text and 0x20 <= ord(text[0]) <= 0x7E:
            self.char_typed.emit(text[0])
        super().keyPressEvent(event)


class CWKeyboard(QWidget):
    """Type-ahead CW keyboard.

    Signals
    -------
    char_sent(str):
        Emitted for each character sent to the keyer.
    stop_requested:
        Emitted when the user clicks Stop or presses Escape.
    """

    char_sent = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label = QLabel("CW:")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self._input = _KeyboardInput()
        self._input.setPlaceholderText("Type here to send CW…")
        self._input.char_typed.connect(self._on_char)
        layout.addWidget(self._input, 1)

        self._sent_label = QLabel("")
        self._sent_label.setStyleSheet("color: #888; font-family: monospace;")
        self._sent_label.setMinimumWidth(200)
        layout.addWidget(self._sent_label)

        clear_btn = QPushButton("Clear")
        clear_btn.setMaximumWidth(50)
        clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(clear_btn)

        stop_btn = QPushButton("■ Stop")
        stop_btn.setMaximumWidth(64)
        stop_btn.setStyleSheet(
            "QPushButton { color: #CC4444; }"
            "QPushButton:hover { background-color: #2D1010; }"
        )
        stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(stop_btn)

        self._sent_chars: list[str] = []

    def _on_char(self, ch: str) -> None:
        self._sent_chars.append(ch.upper())
        if len(self._sent_chars) > 40:
            self._sent_chars = self._sent_chars[-40:]
        self._sent_label.setText("".join(self._sent_chars))
        self.char_sent.emit(ch)

    def _on_clear(self) -> None:
        self._input.clear()
        self._sent_chars.clear()
        self._sent_label.setText("")

    def _on_stop(self) -> None:
        self._input.clear()
        self._sent_chars.clear()
        self._sent_label.setText("")
        self.stop_requested.emit()

    def set_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)
