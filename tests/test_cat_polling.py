"""State polling must not starve the lower-priority polls (mode, S-meter)."""

from __future__ import annotations

import asyncio

import pytest

from fivetr.cat.ftdx10 import FTdx10


class _FakeTransport:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)


def test_poll_round_skips_commands_already_queued():
    radio = FTdx10()
    radio._enqueue_polls()
    first = radio._queue.qsize()
    for _ in range(10):
        radio._enqueue_polls()
    assert radio._queue.qsize() == first


@pytest.mark.asyncio
async def test_mode_and_smeter_polls_are_sent():
    radio = FTdx10()
    radio._transport = _FakeTransport()
    radio._running = True
    tasks = [asyncio.create_task(radio._poll_loop()), asyncio.create_task(radio._send_loop())]
    await asyncio.sleep(1.0)
    radio._running = False
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    written = radio._transport.written
    assert b"MD0;" in written
    assert b"SM0;" in written
    assert radio._queue.qsize() <= len(radio.build_poll_commands())
