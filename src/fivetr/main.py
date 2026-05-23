"""
5trig — entry point.

Bridges Qt's event loop with asyncio via qasync so the CAT engine and
network services (virtual CAT server, DX cluster) run as native coroutines
alongside the GUI.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtWidgets import QApplication

try:
    import qasync
    _QASYNC_AVAILABLE = True
except ImportError:
    _QASYNC_AVAILABLE = False


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libs
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def main() -> None:
    _setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("5trig")
    app.setOrganizationName("5tmuzak")
    app.setApplicationVersion("0.1.0")

    # Import here so Qt app exists before any widget instantiation
    from .ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    if _QASYNC_AVAILABLE:
        # Run Qt + asyncio together
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_forever()
    else:
        logging.warning(
            "qasync not installed — radio I/O will be unavailable. "
            "Install with: pip install qasync"
        )
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
