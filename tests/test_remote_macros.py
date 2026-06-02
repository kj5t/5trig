"""Tests for CW macro protocol over remote client/server."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from fivetr.cat.base import Mode, RadioState
from fivetr.remote.client import RemoteRadio
from fivetr.remote.protocol import recv_msg, send_msg
from fivetr.remote.server import RemoteServer


# ---------------------------------------------------------------------------
# Fake radio for the server side
# ---------------------------------------------------------------------------

class _FakeRadio:
    def __init__(self):
        self.state = RadioState(model="FakeRadio")
        self.state.connected = True
        self.state.mode = Mode.CW


# ---------------------------------------------------------------------------
# Fake WinKeyer
# ---------------------------------------------------------------------------

class _FakeWinKeyer:
    def __init__(self):
        self.is_open = True
        self.calls: list[tuple[str, ...]] = []

    def clear_buffer(self):
        self.calls.append(("clear_buffer",))

    def send_text(self, text: str):
        self.calls.append(("send_text", text))

    def key_immediate(self, down: bool):
        self.calls.append(("key_immediate", down))


# ---------------------------------------------------------------------------
# Server dispatch tests (unit)
# ---------------------------------------------------------------------------

class TestServerDispatchMacros:
    """Verify RemoteServer._dispatch routes cw_text/cw_stop to WinKeyer."""

    def _make_server(self, winkeyer=None):
        radio = _FakeRadio()
        return RemoteServer(radio, audio_port=None, winkeyer=winkeyer)

    @pytest.mark.asyncio
    async def test_cw_text_sends_to_winkeyer(self):
        wk = _FakeWinKeyer()
        srv = self._make_server(winkeyer=wk)
        await srv._dispatch({"t": "cw_text", "text": "CQ CQ DE W1AW K"})
        assert ("clear_buffer",) in wk.calls
        assert ("send_text", "CQ CQ DE W1AW K") in wk.calls

    @pytest.mark.asyncio
    async def test_cw_stop_clears_buffer(self):
        wk = _FakeWinKeyer()
        srv = self._make_server(winkeyer=wk)
        await srv._dispatch({"t": "cw_stop"})
        assert ("clear_buffer",) in wk.calls

    @pytest.mark.asyncio
    async def test_cw_text_noop_without_winkeyer(self):
        srv = self._make_server(winkeyer=None)
        await srv._dispatch({"t": "cw_text", "text": "TEST"})

    @pytest.mark.asyncio
    async def test_cw_text_noop_when_winkeyer_closed(self):
        wk = _FakeWinKeyer()
        wk.is_open = False
        srv = self._make_server(winkeyer=wk)
        await srv._dispatch({"t": "cw_text", "text": "TEST"})
        assert wk.calls == []

    @pytest.mark.asyncio
    async def test_cw_stop_noop_without_winkeyer(self):
        srv = self._make_server(winkeyer=None)
        await srv._dispatch({"t": "cw_stop"})


# ---------------------------------------------------------------------------
# Round-trip over TCP (integration)
# ---------------------------------------------------------------------------

class TestRemoteMacroRoundTrip:
    """Full TCP round-trip: RemoteRadio client → RemoteServer → WinKeyer."""

    @pytest.mark.asyncio
    async def test_cw_text_round_trip(self):
        wk = _FakeWinKeyer()
        radio = _FakeRadio()
        srv = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None, winkeyer=wk)
        await srv.start()
        port = srv._server.sockets[0].getsockname()[1]

        client = RemoteRadio("127.0.0.1", port)
        await client.connect()

        client.cw_send_text("CQ TEST DE W1AW")
        await asyncio.sleep(0.1)

        await client.disconnect()
        await srv.stop()

        assert ("clear_buffer",) in wk.calls
        assert ("send_text", "CQ TEST DE W1AW") in wk.calls

    @pytest.mark.asyncio
    async def test_cw_stop_round_trip(self):
        wk = _FakeWinKeyer()
        radio = _FakeRadio()
        srv = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None, winkeyer=wk)
        await srv.start()
        port = srv._server.sockets[0].getsockname()[1]

        client = RemoteRadio("127.0.0.1", port)
        await client.connect()

        client.cw_stop()
        await asyncio.sleep(0.1)

        await client.disconnect()
        await srv.stop()

        assert ("clear_buffer",) in wk.calls
