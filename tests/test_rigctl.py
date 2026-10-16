"""Tests for the Hamlib rigctld ("NET rigctl") protocol on the virtual CAT server."""

import asyncio

import pytest

from fivetr.cat.base import VFO, Mode
from fivetr.cat.ft710 import FT710
from fivetr.vcat.rigctl import RigctlHandler
from fivetr.vcat.server import VCATServer


class RecordingRadio(FT710):
    """FT710 with the serial side stubbed out; records what would be sent."""

    def __init__(self) -> None:
        super().__init__()
        self.freqs: list[tuple[int, VFO]] = []
        self.modes: list[Mode] = []
        self.ptt_calls: list[bool] = []

    def set_freq(self, hz, vfo=VFO.A):
        self.freqs.append((hz, vfo))

    def set_mode(self, mode):
        self.modes.append(mode)

    def set_ptt(self, tx):
        self.ptt_calls.append(tx)


@pytest.fixture
def radio():
    r = RecordingRadio()
    r.state.vfo_a_hz = 14_074_000
    r.state.vfo_b_hz = 7_074_000
    r.state.mode = Mode.DATA_USB
    return r


@pytest.fixture
def handler(radio):
    return RigctlHandler(radio)


class TestRigctlHandler:
    def test_get_freq_vfo_a(self, handler):
        assert handler.process("f") == "14074000\n"

    def test_get_freq_vfo_b(self, handler, radio):
        radio.state.active_vfo = VFO.B
        assert handler.process("f") == "7074000\n"

    def test_long_name_matches_short(self, handler):
        assert handler.process("\\get_freq") == handler.process("f")

    def test_set_freq_goes_to_active_vfo(self, handler, radio):
        assert handler.process("F 7074000") == "RPRT 0\n"
        assert radio.freqs == [(7_074_000, VFO.A)]

    def test_set_freq_accepts_decimal_and_vfo_prefix(self, handler, radio):
        assert handler.process("F VFOA 14074000.000000") == "RPRT 0\n"
        assert radio.freqs == [(14_074_000, VFO.A)]

    def test_set_freq_rejects_garbage(self, handler, radio):
        assert handler.process("F banana") == "RPRT -1\n"
        assert radio.freqs == []

    def test_get_mode_data_usb_is_pktusb(self, handler):
        assert handler.process("m") == "PKTUSB\n3000\n"

    def test_get_mode_usb(self, handler, radio):
        radio.state.mode = Mode.USB
        assert handler.process("m") == "USB\n2400\n"

    def test_set_mode_pktusb(self, handler, radio):
        assert handler.process("M PKTUSB 3000") == "RPRT 0\n"
        assert radio.modes == [Mode.DATA_USB]

    def test_set_mode_with_vfo_prefix(self, handler, radio):
        assert handler.process("M VFOA CW 500") == "RPRT 0\n"
        assert radio.modes == [Mode.CW]

    def test_set_mode_unknown_is_einval(self, handler, radio):
        assert handler.process("M WARP9 1") == "RPRT -1\n"
        assert radio.modes == []

    def test_ptt_get_and_set(self, handler, radio):
        assert handler.process("t") == "0\n"
        assert handler.process("T 1") == "RPRT 0\n"
        assert handler.process("T 0") == "RPRT 0\n"
        assert radio.ptt_calls == [True, False]
        radio.state.ptt = True
        assert handler.process("t") == "1\n"

    def test_ptt_mic_and_data_variants_count_as_tx(self, handler, radio):
        handler.process("T 2")
        handler.process("T 3")
        assert radio.ptt_calls == [True, True]

    def test_chk_vfo_and_powerstat(self, handler):
        assert handler.process("\\chk_vfo") == "0\n"
        assert handler.process("\\get_powerstat") == "1\n"

    def test_lock_mode_needs_both_lines(self, handler):
        # Hamlib's set_mode blocks until it has read "0" and "RPRT 0".
        assert handler.process("\\get_lock_mode") == "0\nRPRT 0\n"
        assert handler.process("\\set_lock_mode 0") == "RPRT 0\n"

    def test_dump_state_shape(self, handler):
        text = handler.process("\\dump_state")
        lines = text.splitlines()
        assert lines[:3] == ["1", "2", "0"]          # protocol, model, ITU region
        assert lines[-1] == "done"
        assert "has_get_freq=1" in lines
        assert "0 0 0 0 0 0 0" in lines              # range-list terminators

    def test_split_reports_off(self, handler):
        assert handler.process("s") == "0\nVFOA\n"

    def test_unknown_command_is_enimpl(self, handler):
        assert handler.process("\\do_a_barrel_roll") == "RPRT -11\n"
        assert handler.process("S 1 VFOB") == "RPRT -11\n"

    def test_blank_line_is_ignored(self, handler):
        assert handler.process("   ") == ""

    def test_quit_replies_then_closes(self, handler):
        assert not handler.closing
        assert handler.process("q") == "RPRT 0\n"
        assert handler.closing


async def _talk(port: int, payload: bytes, read_until: bytes | None = None) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    if read_until is None:
        data = await asyncio.wait_for(reader.read(65536), 2)
    else:
        data = await asyncio.wait_for(reader.readuntil(read_until), 2)
    writer.close()
    await writer.wait_closed()
    return data


@pytest.fixture
async def running_server(radio):
    server = VCATServer(radio, port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    yield port
    await server.stop()


class TestServerDialects:
    async def test_rigctld_client_over_tcp(self, running_server):
        data = await _talk(running_server, b"\\chk_vfo\nf\n", read_until=b"14074000\n")
        assert data == b"0\n14074000\n"

    async def test_commands_split_across_packets(self, running_server):
        reader, writer = await asyncio.open_connection("127.0.0.1", running_server)
        writer.write(b"\\chk_vfo\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 2) == b"0\n"
        writer.write(b"f")                    # half a command...
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(b"\n")                   # ...finished later
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 2) == b"14074000\n"
        writer.close()
        await writer.wait_closed()

    async def test_quit_closes_the_connection(self, running_server):
        reader, writer = await asyncio.open_connection("127.0.0.1", running_server)
        writer.write(b"\\chk_vfo\nq\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 2)   # read() returns at EOF
        assert data == b"0\nRPRT 0\n"
        writer.close()

    async def test_kenwood_client_still_works_on_same_port(self, running_server):
        data = await _talk(running_server, b"FA;", read_until=b";")
        assert data == b"FA00014074000;"


class TestWithRemoteRadio:
    """In client mode the laptop's vCAT drives a RemoteRadio, not a serial radio."""

    def test_commands_reach_remote_radio_frames(self):
        from fivetr.remote.client import RemoteRadio

        radio = RemoteRadio("shack.invalid")
        sent: list[dict] = []
        radio._send_nowait = sent.append          # capture the JSON frames
        radio.state.vfo_a_hz = 14_074_000
        handler = RigctlHandler(radio)

        assert handler.process("f") == "14074000\n"
        assert handler.process("F 7074000") == "RPRT 0\n"
        assert handler.process("M PKTUSB 3000") == "RPRT 0\n"
        assert handler.process("T 1") == "RPRT 0\n"
        assert sent == [
            {"t": "set_freq", "vfo": "A", "hz": 7_074_000},
            {"t": "set_mode", "code": Mode.DATA_USB.value},
            {"t": "set_ptt", "on": True},
        ]
