"""
CW keyer subsystem.

MidiInput  — reads a MIDI port in a background daemon thread
IambicKeyer — asyncio-based Curtis iambic state machine (Mode A / B / Straight)
CWKeyer    — coordinator: MIDI → iambic → radio PTT / cw_key

Supported hardware
------------------
Any USB MIDI device that sends note_on / note_off messages:
  • Vail Adapter   (note 36 = dit, note 37 = dah by default)
  • WinKeyer USB   (when configured for MIDI output mode)
  • Custom MIDI keyer paddles, foot switches, etc.

The note numbers for dit and dah are configurable from Settings → Keyer.
"""

from .iambic import IambicKeyer, KeyerMode
from .keyer import CWKeyer
from .midi_input import MidiInput

__all__ = ["CWKeyer", "IambicKeyer", "KeyerMode", "MidiInput"]
