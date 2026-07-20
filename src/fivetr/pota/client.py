"""
POTA (Parks on the Air) spot API client.

Polls the public activator spot feed on an interval and emits PotaSpot
objects for spots not already seen, via registered callbacks.

API: https://api.pota.app/spot/activator
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import httpx

from .spot import PotaSpot

logger = logging.getLogger(__name__)

SpotCallback = Callable[[PotaSpot], None]

_SPOT_URL = "https://api.pota.app/spot/activator"
_MAX_SEEN_IDS = 5000


class PotaClient:
    """Async polling client for the POTA activator spot API."""

    def __init__(self, poll_interval: float = 60.0) -> None:
        self.poll_interval = poll_interval

        self._spot_callbacks: list[SpotCallback] = []
        self._running = False
        self._seen_ids: set[int] = set()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_spot_callback(self, cb: SpotCallback) -> None:
        self._spot_callbacks.append(cb)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while self._running:
                try:
                    await self._poll_once(client)
                except httpx.HTTPError as e:
                    logger.warning("POTA spot fetch failed: %s", e)
                except Exception:
                    logger.exception("POTA client error")

                if not self._running:
                    break
                await asyncio.sleep(self.poll_interval)

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(_SPOT_URL)
        resp.raise_for_status()
        data = resp.json()

        for raw in data:
            spot_id = raw.get("spotId")
            if spot_id is not None and spot_id in self._seen_ids:
                continue

            spot = PotaSpot.from_json(raw)
            if spot is None:
                continue

            if spot_id is not None:
                self._seen_ids.add(spot_id)

            for cb in self._spot_callbacks:
                try:
                    cb(spot)
                except Exception:
                    logger.exception("Spot callback error")

        if len(self._seen_ids) > _MAX_SEEN_IDS:
            self._seen_ids = set(list(self._seen_ids)[-2000:])
