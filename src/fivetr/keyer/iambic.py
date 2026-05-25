"""
Software iambic CW keyer state machine.

Runs as an asyncio Task.  Paddle state is updated from the MIDI thread
via set_dit() / set_dah() (simple bool writes — atomic in CPython).
The key_callback is invoked from the asyncio event loop for each
key-down and key-up transition.

Curtis Mode A
-------------
When the squeeze is released (both paddles up), the current element
completes normally.  No additional element is inserted.

Curtis Mode B
-------------
When the squeeze is released during an element, one extra element of the
opposite type is inserted after the current element completes.  This
gives a more natural "squeeze and release" feel that many operators prefer.

Straight key
------------
Paddle events pass through directly without any iambic timing.  Either
dit or dah note triggers key-down; releasing that note triggers key-up.
Useful when the adapter is a single-lever key or thumb switch.

Timing
------
One dit-unit (ms) = 1200 / WPM   (standard PARIS word count)
  dit  = 1 unit
  dah  = 3 units
  inter-element gap = 1 unit
"""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import Callable

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.002   # 2 ms — polling resolution inside element timing


class KeyerMode(enum.Enum):
    STRAIGHT = "straight"
    IAMBIC_A = "iambic_a"
    IAMBIC_B = "iambic_b"


class IambicKeyer:
    """Software iambic keyer (asyncio task).

    Parameters
    ----------
    key_callback:
        ``callback(down: bool)`` — called on key-down (True) and key-up (False).
        Runs on the asyncio event loop.
    wpm:
        Initial speed in words per minute.
    mode:
        KeyerMode.STRAIGHT, .IAMBIC_A, or .IAMBIC_B.
    """

    def __init__(
        self,
        key_callback: Callable[[bool], None],
        wpm: int = 20,
        mode: KeyerMode = KeyerMode.IAMBIC_A,
    ) -> None:
        self._cb = key_callback
        self.wpm: int = wpm
        self.mode: KeyerMode = mode

        # Paddle state — set from the MIDI thread, polled by the asyncio task.
        # CPython bool assignments are atomic; no lock needed.
        self._dit_held: bool = False
        self._dah_held: bool = False

        self._task: asyncio.Task | None = None
        self._key_down: bool = False   # track current physical key state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the keyer state machine task."""
        self._task = asyncio.create_task(
            self._keyer_loop(), name="iambic-keyer"
        )

    async def stop(self) -> None:
        """Stop the keyer and ensure the key is released."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._release()

    # ------------------------------------------------------------------
    # Paddle interface  (called from MIDI thread)
    # ------------------------------------------------------------------

    def set_dit(self, pressed: bool) -> None:
        """Dit paddle pressed (True) or released (False)."""
        if self.mode == KeyerMode.STRAIGHT:
            # Straight key: use call_soon_threadsafe to fire callback safely
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(self._cb, pressed)
            except RuntimeError:
                pass
        else:
            self._dit_held = pressed

    def set_dah(self, pressed: bool) -> None:
        """Dah paddle pressed (True) or released (False)."""
        if self.mode == KeyerMode.STRAIGHT:
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(self._cb, pressed)
            except RuntimeError:
                pass
        else:
            self._dah_held = pressed

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    @property
    def _unit_s(self) -> float:
        """One dit-unit in seconds."""
        return 1.2 / max(1, self.wpm)

    def _assert(self) -> None:
        if not self._key_down:
            self._key_down = True
            self._cb(True)

    def _release(self) -> None:
        if self._key_down:
            self._key_down = False
            self._cb(False)

    async def _sleep_unit(self, units: float) -> bool:
        """Sleep for units × dit-length.

        Polls every POLL_INTERVAL for early cancellation.
        Returns True if sleep completed normally, False if cancelled.
        """
        remaining = units * self._unit_s
        while remaining > 0:
            step = min(POLL_INTERVAL, remaining)
            await asyncio.sleep(step)
            remaining -= step
        return True

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def _keyer_loop(self) -> None:
        last_element: str | None = None   # 'dit' or 'dah'

        while True:
            try:
                # ---- IDLE: wait for a paddle press ----
                while not self._dit_held and not self._dah_held:
                    await asyncio.sleep(POLL_INTERVAL)
                    last_element = None   # clear memory while truly idle

                # ---- ACTIVE: send elements until paddles quiet ----
                extra_element: str | None = None   # Mode B one-shot memory

                while True:
                    dit = self._dit_held
                    dah = self._dah_held

                    # --- Pick next element to send ---
                    if extra_element:
                        element = extra_element
                        extra_element = None
                    elif dit and dah:
                        # Squeeze: alternate, opposite of what was just sent
                        element = "dah" if last_element == "dit" else "dit"
                    elif dit:
                        element = "dit"
                    elif dah:
                        element = "dah"
                    else:
                        break   # both released, exit active loop

                    # --- Send the element ---
                    duration_units = 1.0 if element == "dit" else 3.0
                    self._assert()

                    # Monitor for opposite paddle press DURING this element
                    opposite_seen = False
                    unit = self._unit_s
                    elapsed = 0.0
                    while elapsed < duration_units * unit:
                        step = min(POLL_INTERVAL, duration_units * unit - elapsed)
                        await asyncio.sleep(step)
                        elapsed += step
                        if element == "dit" and self._dah_held:
                            opposite_seen = True
                        elif element == "dah" and self._dit_held:
                            opposite_seen = True

                    self._release()
                    last_element = element

                    # --- Inter-element gap ---
                    await asyncio.sleep(self._unit_s)

                    # --- Mode B memory: queue one extra element ---
                    if (
                        self.mode == KeyerMode.IAMBIC_B
                        and opposite_seen
                        and not self._dit_held
                        and not self._dah_held
                    ):
                        extra_element = "dah" if element == "dit" else "dit"

                # Key is already up here; loop back to idle

            except asyncio.CancelledError:
                self._release()
                raise
            except Exception:
                logger.exception("Iambic keyer state machine error")
                self._release()
                await asyncio.sleep(0.1)
