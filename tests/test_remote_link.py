"""Link supervision for the remote (shack <-> laptop) connection.

Regression: after the laptop moved from wired to WiFi, the old TCP connection stayed
"established" but dead, commands went into it, and the radio never changed frequency.
"""

import asyncio
import logging
import socket

from fivetr.cat.base import RadioEvent
from fivetr.cat.ft710 import FT710
from fivetr.remote.client import RemoteRadio
from fivetr.remote.protocol import configure_keepalive, recv_msg, send_msg
from fivetr.remote.server import RemoteServer
from fivetr.vcat.rigctl import RigctlHandler


async def until(cond, timeout=3.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


class FakeShack:
    """A shack server we can make go silent, drop connections, or speak the old protocol."""

    def __init__(self, heartbeat=True):
        self.heartbeat = heartbeat
        self.pong = True
        self.frames: list[dict] = []
        self.connections = 0
        self.writers: list[asyncio.StreamWriter] = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        hello = {"t": "hello", "model": "FTdx10", "ver": 1}
        if self.heartbeat:
            hello["heartbeat"] = True
        await send_msg(writer, hello)
        try:
            while True:
                msg = await recv_msg(reader)
                self.frames.append(msg)
                if msg["t"] == "ping" and self.pong:
                    await send_msg(writer, {"t": "pong", "n": msg["n"]})
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass


def fast_radio(port):
    radio = RemoteRadio("127.0.0.1", port)
    radio.HEARTBEAT_INTERVAL = 0.05
    radio.HEARTBEAT_TIMEOUT = 0.3
    radio.RECONNECT_DELAYS = (0.05,)
    radio.events = []

    async def listener(event, payload):
        radio.events.append(event)

    radio.add_listener(listener)
    return radio


def commands(shack):
    return [f for f in shack.frames if f["t"] != "ping"]


class TestClientSupervision:
    async def test_silent_shack_is_detected_and_the_link_reopens(self):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        try:
            await radio.connect()
            assert radio.link_up and radio.state.connected
            shack.pong = False                       # network died: nothing comes back
            assert await until(lambda: radio.reconnecting)
            assert not radio.state.connected
            assert RadioEvent.DISCONNECTED in radio.events
            shack.pong = True                        # network is back
            assert await until(lambda: radio.link_up and radio.state.connected)
            assert shack.connections >= 2
            assert radio.events.count(RadioEvent.CONNECTED) >= 2
            radio.set_freq(7_074_000)
            assert await until(lambda: any(f["t"] == "set_freq" for f in shack.frames))
        finally:
            await radio.disconnect()
            await shack.stop()

    async def test_shack_closing_the_connection_reopens_it(self):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        try:
            await radio.connect()
            shack.writers[0].close()
            assert await until(lambda: shack.connections >= 2 and radio.link_up and radio.state.connected)
        finally:
            await radio.disconnect()
            await shack.stop()

    async def test_old_shack_without_heartbeat_is_never_timed_out(self):
        shack = FakeShack(heartbeat=False)
        await shack.start()
        radio = fast_radio(shack.port)
        try:
            await radio.connect()
            await asyncio.sleep(0.8)                 # well past HEARTBEAT_TIMEOUT, no pongs
            assert radio.link_up and radio.state.connected
            assert shack.connections == 1
            assert not any(f["t"] == "ping" for f in shack.frames)
        finally:
            await radio.disconnect()
            await shack.stop()

    async def test_commands_while_down_are_dropped_not_queued(self, caplog):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        radio.RECONNECT_DELAYS = (30.0,)             # stay down for the whole test
        try:
            await radio.connect()
            shack.pong = False
            assert await until(lambda: radio.reconnecting)
            with caplog.at_level(logging.WARNING):
                radio.set_freq(7_074_000)
            await asyncio.sleep(0.1)
            assert not any(f["t"] == "set_freq" for f in shack.frames)
            assert "dropped command" in caplog.text
        finally:
            await radio.disconnect()
            await shack.stop()

    async def test_ptt_is_released_after_reconnecting(self):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        try:
            await radio.connect()
            radio.set_ptt(True)
            assert await until(lambda: any(f.get("on") is True for f in shack.frames))
            shack.pong = False
            assert await until(lambda: radio.reconnecting)
            shack.pong = True
            assert await until(lambda: radio.link_up)
            assert await until(lambda: any(f["t"] == "set_ptt" and f["on"] is False for f in shack.frames))
            assert radio.ptt_requested is False
        finally:
            await radio.disconnect()
            await shack.stop()

    async def test_disconnect_while_reconnecting_stops_for_good(self):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        radio.RECONNECT_DELAYS = (0.05,)
        try:
            await radio.connect()
            shack.pong = False
            assert await until(lambda: radio.reconnecting)
            await asyncio.wait_for(radio.disconnect(), 3)
            n = shack.connections
            await asyncio.sleep(0.3)
            assert shack.connections == n            # no reconnect after an explicit disconnect
            assert not radio.link_up or not radio.state.connected
        finally:
            await shack.stop()

    async def test_auto_reconnect_can_be_turned_off(self):
        shack = FakeShack()
        await shack.start()
        radio = fast_radio(shack.port)
        radio.auto_reconnect = False
        try:
            await radio.connect()
            shack.writers[0].close()
            assert await until(lambda: not radio.state.connected)
            await asyncio.sleep(0.3)
            assert shack.connections == 1 and not radio.reconnecting
        finally:
            await radio.disconnect()
            await shack.stop()


class RecordingRadio(FT710):
    def __init__(self):
        super().__init__()
        self.ptt_calls = []

    def set_ptt(self, tx):
        self.ptt_calls.append(tx)


async def _connect(server):
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    return reader, writer, await recv_msg(reader)


class TestServerSupervision:
    async def test_hello_advertises_heartbeat_and_ping_gets_pong(self):
        server = RemoteServer(RecordingRadio(), host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        try:
            reader, writer, hello = await _connect(server)
            assert hello["heartbeat"] is True
            await send_msg(writer, {"t": "ping", "n": 7})
            while True:                               # skip the initial state frame
                msg = await asyncio.wait_for(recv_msg(reader), 2)
                if msg["t"] == "pong":
                    break
            assert msg == {"t": "pong", "n": 7}
            writer.close()
        finally:
            await server.stop()

    async def test_client_that_pings_then_goes_silent_is_dropped(self):
        radio = RecordingRadio()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None)
        server.CLIENT_IDLE_TIMEOUT = 0.3
        await server.start()
        try:
            reader, writer, _ = await _connect(server)
            await send_msg(writer, {"t": "set_ptt", "on": True})
            await send_msg(writer, {"t": "ping", "n": 1})
            assert await until(lambda: server.client_count == 1)
            # ...and now it sends nothing more (its network vanished).
            assert await until(lambda: server.client_count == 0, timeout=2)
            assert radio.ptt_calls == [True, False]   # radio released with its last client
            writer.close()
        finally:
            await server.stop()

    async def test_legacy_client_that_never_pings_is_not_timed_out(self):
        server = RemoteServer(RecordingRadio(), host="127.0.0.1", port=0, audio_port=None)
        server.CLIENT_IDLE_TIMEOUT = 0.2
        await server.start()
        try:
            _, writer, _ = await _connect(server)
            await asyncio.sleep(0.6)
            assert server.client_count == 1
            writer.close()
        finally:
            await server.stop()


class TestKeepalive:
    def test_options_are_set_on_a_real_socket(self):
        s = socket.socket()
        try:
            configure_keepalive(s)
            assert s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
            if hasattr(socket, "TCP_USER_TIMEOUT"):
                assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) > 0
            if hasattr(socket, "TCP_KEEPIDLE"):
                assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 10
        finally:
            s.close()

    def test_none_socket_is_ignored(self):
        configure_keepalive(None)


class TestVcatWhileLinkDown:
    def test_set_commands_fail_loudly_instead_of_pretending(self):
        radio = RemoteRadio("shack.invalid")
        sent: list[dict] = []
        radio._send_nowait = sent.append
        handler = RigctlHandler(radio)

        radio.reconnecting = True
        assert handler.process("F 7074000") == "RPRT -6\n"
        assert handler.process("M PKTUSB 3000") == "RPRT -6\n"
        assert handler.process("T 1") == "RPRT -6\n"
        assert sent == []

        radio.reconnecting = False
        assert handler.process("F 7074000") == "RPRT 0\n"
        assert sent == [{"t": "set_freq", "vfo": "A", "hz": 7_074_000}]

    def test_local_radio_is_unaffected(self):
        radio = RecordingRadio()                      # has no link_up attribute
        calls = []
        radio.set_freq = lambda hz, vfo=None: calls.append(hz)
        assert RigctlHandler(radio).process("F 14074000") == "RPRT 0\n"
        assert calls == [14_074_000]
