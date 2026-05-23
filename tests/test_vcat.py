"""Tests for the virtual CAT TS-2000 command processor."""

import pytest
from fivetr.cat.base import Mode, RadioState, VFO
from fivetr.cat.ft710 import FT710
from fivetr.vcat.server import VCATServer


class FakeRadio(FT710):
    """FT710 with connection skipped — for unit tests."""
    pass


@pytest.fixture
def server():
    radio = FakeRadio()
    radio.state.vfo_a_hz = 14_074_000
    radio.state.vfo_b_hz = 7_074_000
    radio.state.mode = Mode.USB
    radio.state.ptt = False
    return VCATServer(radio)


class TestVCATServer:
    def test_fa_query(self, server):
        reply = server._process("FA")
        assert reply == "FA00014074000;"

    def test_fb_query(self, server):
        reply = server._process("FB")
        assert reply == "FB00007074000;"

    def test_md_query_usb(self, server):
        reply = server._process("MD")
        # USB in TS-2000 is mode 3
        assert reply == "MD3;"

    def test_tx_query_off(self, server):
        reply = server._process("TX")
        assert reply == "TX0;"

    def test_tx_on(self, server):
        server._radio.state.ptt = True
        reply = server._process("TX")
        assert reply == "TX1;"

    def test_id_query(self, server):
        assert server._process("ID") == "ID019;"

    def test_ps_query(self, server):
        assert server._process("PS") == "PS1;"

    def test_ai_query(self, server):
        assert server._process("AI0") == "AI0;"

    def test_if_query_returns_string(self, server):
        reply = server._process("IF")
        assert reply is not None
        assert reply.startswith("IF")
        assert reply.endswith(";")
        assert "14074000" in reply

    def test_split_commands(self):
        result = VCATServer._split_commands("FA;FB;MD;")
        assert result == ["FA", "FB", "MD"]

    def test_unknown_command_returns_question(self, server):
        reply = server._process("XX99")
        assert reply == "?;"
