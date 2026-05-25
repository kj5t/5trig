"""
MIDI input listener for CW keyer paddles.

Opens a MIDI input port using mido + python-rtmidi and dispatches
note-on / note-off events to a registered callback.  Runs the mido
blocking iteration in a daemon background thread so it never touches
the Qt or asyncio event loops directly.

Vail adapter MIDI mapping (configurable via KeyerConfig):
  Default dit note : 36  (C2 — left paddle)
  Default dah note : 37  (C#2 — right paddle)

WinKeyer USB in MIDI mode uses the same note numbering.  Other devices
can be remapped from Settings → Keyer.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# Callback signature: (note_number: int, pressed: bool)
NoteCallback = Callable[[int, bool], None]


class MidiInput:
    """Background MIDI input port listener.

    Parameters
    ----------
    port_name:
        Exact mido port name string (e.g. ``"Vail Adapter MIDI 1:0"``).
        Pass ``None`` or ``""`` to use the first available input port.
    callback:
        Called from the reader thread: ``callback(note: int, pressed: bool)``.
        Keep it short — it runs in a non-asyncio thread.
    """

    def __init__(
        self,
        port_name: str | None = None,
        callback: NoteCallback | None = None,
    ) -> None:
        self._port_name = port_name or None
        self._callback = callback
        self._port = None
        self._thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the MIDI port and begin reading (non-blocking call)."""
        try:
            import mido
        except ImportError:
            logger.error(
                "mido not installed — MIDI keyer unavailable.  "
                "pip install mido python-rtmidi"
            )
            return

        available = mido.get_input_names()
        if not available:
            logger.warning("MIDI: no input ports found")
            return

        target = self._port_name
        if not target:
            # Skip the ALSA "Midi Through" passthrough if others exist
            non_through = [p for p in available if "Through" not in p]
            target = non_through[0] if non_through else available[0]
            logger.info("MIDI: auto-selected port %r", target)

        if target not in available:
            logger.error("MIDI port not found: %r  (available: %s)", target, available)
            return

        try:
            self._port = mido.open_input(target)
        except Exception as exc:
            logger.error("Failed to open MIDI port %r: %s", target, exc)
            return

        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True, name="midi-rx")
        self._thread.start()
        logger.info("MIDI keyer listening on %r", target)

    def stop(self) -> None:
        """Close the MIDI port and stop the reader thread."""
        self._running = False
        if self._port:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
        # Thread exits because port iteration ends when port is closed

    @property
    def is_open(self) -> bool:
        return self._running and self._port is not None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reader(self) -> None:
        """Daemon thread: iterate MIDI messages and dispatch to callback."""
        try:
            for msg in self._port:     # blocking iteration; exits when port closes
                if not self._running:
                    break
                self._dispatch(msg)
        except Exception as exc:
            if self._running:
                logger.error("MIDI reader error: %s", exc)

    def _dispatch(self, msg) -> None:
        if self._callback is None:
            return
        # note_on with velocity 0 == note_off (MIDI Running Status convention)
        if msg.type == "note_on" and msg.velocity > 0:
            self._callback(msg.note, True)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            self._callback(msg.note, False)
        # CC messages: some keyers send CC#1 (mod wheel) for key state
        elif msg.type == "control_change" and msg.control == 1:
            self._callback(0, msg.value > 0)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_ports() -> list[str]:
        """Return all available MIDI input port names.  Returns [] if mido missing."""
        try:
            import mido
            return mido.get_input_names()
        except ImportError:
            return []

    @staticmethod
    def probe_vail() -> str | None:
        """Return the port name if a Vail adapter is detected, else None."""
        for p in MidiInput.list_ports():
            if "vail" in p.lower() or "adapter" in p.lower():
                return p
        return None
