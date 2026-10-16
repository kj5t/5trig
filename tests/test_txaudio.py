"""Remote transmit audio: gating, sender filtering and shack-side safety rules."""

import asyncio
import struct

import pytest

from fivetr.cat.ft710 import FT710
from fivetr.remote import txaudio
from fivetr.remote.protocol import recv_msg, send_msg
from fivetr.remote.server import RemoteServer
from fivetr.remote.txaudio import (
    FRAME_BYTES,
    PREBUFFER_FRAMES,
    TxAudioReceiver,
    TxGate,
)

PCM = bytes(range(256)) * (FRAME_BYTES // 256) + bytes(FRAME_BYTES % 256)


def packet(seq: int, payload: bytes = PCM) -> bytes:
    return struct.pack(">I", seq) + payload


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestTxGate:
    def test_open_while_active_then_holds_then_closes(self):
        clock, active = Clock(), {"on": False}
        gate = TxGate(lambda: active["on"], hold=0.5, clock=clock)
        assert not gate.is_open()                  # never keyed
        active["on"] = True
        assert gate.is_open()
        active["on"] = False
        clock.now += 0.3
        assert gate.is_open()                      # tail after PTT drops
        clock.now += 0.3
        assert not gate.is_open()                  # tail over


def make_receiver(allowed=("10.0.0.5",), gate_open=True):
    return TxAudioReceiver(
        "127.0.0.1", 0, "radio_sink",
        allowed_ips=lambda: set(allowed),
        gate=TxGate(lambda: gate_open),
    )


class TestReceiverAccept:
    def test_plays_audio_from_connected_client_while_keyed(self):
        rx = make_receiver()
        assert rx.accept(packet(1), "10.0.0.5") == PCM

    def test_foreign_ip_is_dropped(self):
        rx = make_receiver()
        assert rx.accept(packet(1), "10.0.0.99") is None
        assert rx.dropped_foreign == 1

    def test_no_clients_means_nothing_is_accepted(self):
        rx = make_receiver(allowed=())
        assert rx.accept(packet(1), "10.0.0.5") is None

    def test_not_keyed_is_dropped(self):
        rx = make_receiver(gate_open=False)
        assert rx.accept(packet(1), "10.0.0.5") is None
        assert rx.dropped_ungated == 1

    @pytest.mark.parametrize("data", [b"", b"\x00\x00\x00\x01", packet(1, b"\x00" * 3),
                                      packet(1, b"\x00" * 5000)])
    def test_malformed_datagrams_are_dropped(self, data):
        rx = make_receiver()
        assert rx.accept(data, "10.0.0.5") is None
        assert rx.dropped_bad == 1

    def test_late_and_duplicate_packets_are_dropped(self):
        rx = make_receiver()
        assert rx.accept(packet(10), "10.0.0.5") is not None
        assert rx.accept(packet(10), "10.0.0.5") is None
        assert rx.accept(packet(9), "10.0.0.5") is None
        assert rx.accept(packet(11), "10.0.0.5") is not None

    def test_sequence_wraparound_is_accepted(self):
        rx = make_receiver()
        assert rx.accept(packet(0xFFFFFFFF), "10.0.0.5") is not None
        assert rx.accept(packet(0), "10.0.0.5") is not None


class TestPrebuffer:
    def test_holds_back_then_releases_everything(self):
        rx = make_receiver()
        out = []
        for i in range(PREBUFFER_FRAMES):
            out.append(rx._prime(bytes([i]) * 4, now=1.0 + i * 0.02))
        assert out[:-1] == [[]] * (PREBUFFER_FRAMES - 1)
        assert len(out[-1]) == PREBUFFER_FRAMES
        assert rx._prime(b"next", now=1.2) == [b"next"]     # then straight through

    def test_new_over_primes_again(self):
        rx = make_receiver()
        for i in range(PREBUFFER_FRAMES):
            rx._prime(b"x" * 4, now=1.0 + i * 0.02)
        assert rx._prime(b"y" * 4, now=5.0) == []           # long silence: refill


class RecordingRadio(FT710):
    def __init__(self):
        super().__init__()
        self.ptt_calls = []

    def set_ptt(self, tx):
        self.ptt_calls.append(tx)
        self.state.ptt = tx


class FakeProc:
    def __init__(self, *a, **k):
        self.stdin = self
    def write(self, b): pass
    def flush(self): pass
    def close(self): pass
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def kill(self): pass


@pytest.fixture
def tx_server(monkeypatch):
    monkeypatch.setattr(txaudio, "pipewire_sink_exists", lambda name: True)
    monkeypatch.setattr(txaudio.subprocess, "Popen", FakeProc)


async def _connect(server):
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    hello = await asyncio.wait_for(recv_msg(reader), 2)
    await asyncio.wait_for(recv_msg(reader), 2)         # initial state
    return reader, writer, hello


class TestRemoteServerTx:
    async def test_hello_offers_tx_port_only_when_receiver_runs(self, tx_server):
        radio = RecordingRadio()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None,
                              tx_audio_port=0, tx_audio_sink="radio_sink")
        await server.start()
        try:
            _, writer, hello = await _connect(server)
            assert hello["tx_audio_udp_port"] == server._tx_receiver.port != 0
            writer.close()
        finally:
            await server.stop()

    async def test_no_tx_port_when_not_configured(self):
        server = RemoteServer(RecordingRadio(), host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        try:
            _, writer, hello = await _connect(server)
            assert "tx_audio_udp_port" not in hello
            writer.close()
        finally:
            await server.stop()

    async def test_missing_sink_means_no_offer_and_an_error(self, monkeypatch):
        monkeypatch.setattr(txaudio, "pipewire_sink_exists", lambda name: False)
        server = RemoteServer(RecordingRadio(), host="127.0.0.1", port=0, audio_port=None,
                              tx_audio_port=0, tx_audio_sink="nope")
        await server.start()
        try:
            _, writer, hello = await _connect(server)
            assert "tx_audio_udp_port" not in hello
            assert "nope" in server.tx_audio_error
            writer.close()
        finally:
            await server.stop()

    async def test_ptt_request_opens_the_gate_and_peer_ip_is_allowed(self, tx_server):
        radio = RecordingRadio()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None,
                              tx_audio_port=0, tx_audio_sink="radio_sink")
        await server.start()
        try:
            _, writer, _ = await _connect(server)
            rx = server._tx_receiver
            assert rx.accept(packet(1), "127.0.0.1") is None        # connected but not keyed
            await send_msg(writer, {"t": "set_ptt", "on": True})
            await asyncio.sleep(0.1)
            assert rx.accept(packet(2), "127.0.0.1") == PCM
            assert rx.accept(packet(3), "10.9.9.9") is None          # not a connected client
            writer.close()
        finally:
            await server.stop()

    async def test_last_client_leaving_releases_ptt(self):
        radio = RecordingRadio()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        try:
            _, writer, _ = await _connect(server)
            await send_msg(writer, {"t": "set_ptt", "on": True})
            await asyncio.sleep(0.1)
            assert radio.ptt_calls == [True]
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)
            assert radio.ptt_calls == [True, False]
        finally:
            await server.stop()

    async def test_client_leaving_when_not_transmitting_does_not_touch_ptt(self):
        radio = RecordingRadio()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        try:
            _, writer, _ = await _connect(server)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)
            assert radio.ptt_calls == []
        finally:
            await server.stop()
