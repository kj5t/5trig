"""
Keyer input listener — MIDI and evdev (Vail HID keyboard) backends.

Supports two types of CW paddle adapters:

* **MIDI** — opens a MIDI input port using mido + python-rtmidi.
  Dispatches note-on / note-off events.  Used by WinKeyer USB (MIDI
  mode) and MIDI-native keyer adapters.

* **evdev / HID keyboard** — reads Linux input events from
  ``/dev/input/eventN``.  The Vail adapter (Adafruit QT Py / Seeed
  XIAO) registers as a USB HID keyboard and sends Left Ctrl (dit) /
  Right Ctrl (dah) key events.  This backend is preferred when a Vail
  device is detected.

Auto-detection order:
  1. If the selected port name matches an evdev device → use evdev.
  2. If the selected port name matches a MIDI port   → use MIDI.
  3. If auto (empty port): scan for Vail HID devices first, then
     fall back to MIDI.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

NoteCallback = Callable[[int, bool], None]

# Vail adapter key codes → synthetic note numbers that match the
# default KeyerConfig (dit_note=36, dah_note=37).
_VAIL_KEYMAP: dict[int, int] = {
    29: 36,     # KEY_LEFTCTRL  → dit
    97: 37,     # KEY_RIGHTCTRL → dah
}


def _find_vail_evdev() -> str | None:
    """Return the evdev path for the first Vail-like HID keyboard, or None."""
    try:
        import evdev
    except ImportError:
        return None
    for path in sorted(evdev.list_devices()):
        try:
            dev = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        name_lower = dev.name.lower()
        if any(tok in name_lower for tok in ("vail", "qt py", "xiao", "adafruit")):
            import evdev.ecodes as e
            caps = dev.capabilities().get(e.EV_KEY, [])
            if e.KEY_LEFTCTRL in caps:
                dev.close()
                return path
        dev.close()
    return None


def _list_evdev_ports() -> list[str]:
    """Return evdev paths for Vail-like HID keyboards."""
    try:
        import evdev
    except ImportError:
        return []
    result = []
    for path in sorted(evdev.list_devices()):
        try:
            dev = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        name_lower = dev.name.lower()
        if any(tok in name_lower for tok in ("vail", "qt py", "xiao", "adafruit")):
            result.append(f"evdev:{dev.name}:{path}")
        dev.close()
    return result


class MidiInput:
    """Background keyer input listener (MIDI or evdev).

    Parameters
    ----------
    port_name:
        Exact mido port name, or ``"evdev:<name>:<path>"`` for an evdev
        device.  Pass ``None`` or ``""`` to auto-detect.
    callback:
        Called from the reader thread: ``callback(note: int, pressed: bool)``.
    """

    def __init__(
        self,
        port_name: str | None = None,
        callback: NoteCallback | None = None,
    ) -> None:
        self._port_name = port_name or None
        self._callback = callback
        self._port = None          # mido port or evdev InputDevice
        self._thread: threading.Thread | None = None
        self._running = False
        self._backend: str = ""    # "midi" or "evdev"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the input device and begin reading."""
        if self._port_name and self._port_name.startswith("evdev:"):
            self._start_evdev(self._port_name.split(":", 2)[2])
            return

        if self._port_name:
            # Explicit port name — try MIDI first, then evdev scan
            self._start_midi()
            return

        # Auto-detect: try Vail HID first, then fall back to MIDI
        evdev_path = _find_vail_evdev()
        if evdev_path:
            self._start_evdev(evdev_path)
            return

        self._start_midi()

    def stop(self) -> None:
        """Close the device and stop the reader thread."""
        self._running = False
        if self._backend == "midi" and self._port:
            try:
                self._port.close()
            except Exception:
                pass
        elif self._backend == "evdev" and self._port:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None
        self._backend = ""

    @property
    def is_open(self) -> bool:
        return self._running and self._port is not None

    # ------------------------------------------------------------------
    # MIDI backend
    # ------------------------------------------------------------------

    def _start_midi(self) -> None:
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

        self._backend = "midi"
        self._running = True
        self._thread = threading.Thread(
            target=self._midi_reader, daemon=True, name="midi-rx"
        )
        self._thread.start()
        logger.info("MIDI keyer listening on %r", target)

    def _midi_reader(self) -> None:
        try:
            for msg in self._port:
                if not self._running:
                    break
                self._dispatch_midi(msg)
        except Exception as exc:
            if self._running:
                logger.error("MIDI reader error: %s", exc)

    def _dispatch_midi(self, msg) -> None:
        if self._callback is None:
            return
        if msg.type == "note_on" and msg.velocity > 0:
            self._callback(msg.note, True)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            self._callback(msg.note, False)
        elif msg.type == "control_change" and msg.control == 1:
            self._callback(0, msg.value > 0)

    # ------------------------------------------------------------------
    # evdev (Vail HID keyboard) backend
    # ------------------------------------------------------------------

    def _start_evdev(self, path: str) -> None:
        try:
            import evdev
        except ImportError:
            logger.error("evdev not installed — pip install evdev")
            return

        try:
            self._port = evdev.InputDevice(path)
        except (PermissionError, OSError) as exc:
            logger.error("Failed to open evdev device %s: %s", path, exc)
            return

        # Grab the device so paddle keystrokes don't leak into the desktop
        try:
            self._port.grab()
        except OSError:
            logger.warning("Could not grab %s — paddle keys may leak to desktop", path)

        self._backend = "evdev"
        self._running = True
        self._thread = threading.Thread(
            target=self._evdev_reader, daemon=True, name="evdev-rx"
        )
        self._thread.start()
        logger.info("Vail keyer (evdev) listening on %s (%s)", self._port.name, path)

    def _evdev_reader(self) -> None:
        import evdev.ecodes as e
        try:
            for event in self._port.read_loop():
                if not self._running:
                    break
                if event.type != e.EV_KEY:
                    continue
                note = _VAIL_KEYMAP.get(event.code)
                if note is None:
                    continue
                if self._callback is None:
                    continue
                if event.value == 1:       # key down
                    self._callback(note, True)
                elif event.value == 0:     # key up
                    self._callback(note, False)
                # value == 2 is autorepeat — ignore
        except OSError:
            if self._running:
                logger.error("evdev device disconnected")
        except Exception as exc:
            if self._running:
                logger.error("evdev reader error: %s", exc)
        finally:
            if self._port:
                try:
                    self._port.ungrab()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_ports() -> list[str]:
        """Return all available input port names (evdev + MIDI)."""
        ports = _list_evdev_ports()
        try:
            import mido
            ports.extend(mido.get_input_names())
        except ImportError:
            pass
        return ports

    @staticmethod
    def probe_vail() -> str | None:
        """Return the port name if a Vail adapter is detected, else None."""
        path = _find_vail_evdev()
        if path:
            return f"evdev:{path}"
        for p in MidiInput.list_ports():
            if "vail" in p.lower() or "adapter" in p.lower():
                return p
        return None
