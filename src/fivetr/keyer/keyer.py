"""
CW keyer coordinator.

Ties together:
  IambicKeyer  ← software iambic state machine with Mode A / B / Straight
  Radio        ← RemoteRadio (cw_key over TCP) or local CATRadio (set_ptt)

Usage (from an asyncio context):
    keyer = CWKeyer(radio, config)
    await keyer.start()
    …
    await keyer.stop()

The keyer also accepts a ``key_state_callback`` that is fired each time
the physical key goes down or up — the main window uses this to update
the LED indicator in the keyer widget.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from .iambic import IambicKeyer, KeyerMode

logger = logging.getLogger(__name__)


class CWKeyer:
    """Iambic engine → radio PTT/CW key.

    Parameters
    ----------
    radio:
        Any object that has either ``cw_key(down: bool)`` (RemoteRadio)
        or ``set_ptt(down: bool)`` (CATRadio).  ``cw_key`` is preferred.
    config:
        A ``KeyerConfig`` dataclass instance from the application settings.
    key_state_callback:
        Optional callable invoked with ``True``/``False`` whenever the
        key goes down / up.  Runs on the asyncio event loop — safe to
        update a Qt widget via a Qt Signal.
    """

    def __init__(
        self,
        radio,
        config,
        key_state_callback: Callable[[bool], None] | None = None,
    ) -> None:
        self._radio = radio
        self._config = config
        self._key_state_cb = key_state_callback
        self._running = False
        self._dit_held = False
        self._dah_held = False
        self._loop: asyncio.AbstractEventLoop | None = None

        mode = KeyerMode(config.mode)
        self._iambic = IambicKeyer(
            key_callback=self._on_key_event,
            wpm=config.wpm,
            mode=mode,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start iambic state machine."""
        self._loop = asyncio.get_event_loop()
        await self._iambic.start()
        self._running = True
        logger.info(
            "CW keyer started — mode=%s  wpm=%d",
            self._config.mode,
            self._config.wpm,
        )

    async def stop(self) -> None:
        """Stop keyer and ensure PTT/key is released."""
        self._running = False
        await self._iambic.stop()
        # Belt-and-suspenders: make sure PTT is off
        try:
            self._send_key(False)
        except Exception:
            pass
        logger.info("CW keyer stopped")

    def update_wpm(self, wpm: int) -> None:
        """Live WPM change — takes effect on the next element boundary."""
        self._iambic.wpm = wpm
        self._config.wpm = wpm

    def update_mode(self, mode: str) -> None:
        """Live mode change — takes effect on the next element boundary."""
        self._iambic.mode = KeyerMode(mode)
        self._config.mode = mode

    # ------------------------------------------------------------------
    # Key event callback  (called from asyncio event loop by IambicKeyer)
    # ------------------------------------------------------------------

    def _on_key_event(self, down: bool) -> None:
        """Called by the iambic state machine for each key-down / key-up."""
        if not getattr(self._radio, "has_winkeyer", False):
            self._send_key(down)
        if self._key_state_cb:
            try:
                self._key_state_cb(down)
            except Exception:
                logger.exception("Key state callback error")

    def _send_key(self, down: bool) -> None:
        """Forward key state to the radio via cw_key() or set_ptt()."""
        radio = self._radio
        if radio is None:
            return
        try:
            if hasattr(radio, "cw_key"):
                radio.cw_key(down)
            else:
                radio.set_ptt(down)
        except Exception:
            logger.exception("Failed to send key event to radio")
