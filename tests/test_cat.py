"""Tests for CAT command builders and reply parsers."""

import pytest
from fivetr.cat.base import Band, Mode, VFO
from fivetr.cat.ft710 import FT710
from fivetr.cat.ftdx10 import FTdx10
from fivetr.cat.ftdx101 import FTdx101


class TestFT710:
    def setup_method(self):
        self.radio = FT710()

    def test_build_set_freq_vfo_a(self):
        raw = self.radio.build_set_freq(VFO.A, 14_074_000)
        assert raw == b"FA014074000;"

    def test_build_set_freq_vfo_b(self):
        raw = self.radio.build_set_freq(VFO.B, 7_074_000)
        assert raw == b"FB007074000;"

    def test_parse_freq_reply(self):
        hz = self.radio.parse_freq_reply(b"FA014074000;")
        assert hz == 14_074_000

    def test_parse_freq_reply_160m(self):
        hz = self.radio.parse_freq_reply(b"FA001840000;")
        assert hz == 1_840_000

    def test_build_set_mode_usb(self):
        raw = self.radio.build_set_mode(Mode.USB)
        assert raw == b"MD02;"

    def test_build_set_mode_cw(self):
        raw = self.radio.build_set_mode(Mode.CW)
        assert raw == b"MD03;"

    def test_parse_mode_reply_lsb(self):
        mode = self.radio.parse_mode_reply(b"MD01;")
        assert mode == Mode.LSB

    def test_parse_mode_reply_ft8_data(self):
        mode = self.radio.parse_mode_reply(b"MD0C;")
        assert mode == Mode.DATA_USB

    def test_build_set_ptt_on(self):
        assert self.radio.build_set_ptt(True) == b"TX1;"

    def test_build_set_ptt_off(self):
        assert self.radio.build_set_ptt(False) == b"TX0;"

    @pytest.mark.asyncio
    async def test_dispatch_freq_reply_updates_state(self):
        self.radio._dispatch_response(b"FA014074000;FB007074000;")
        assert self.radio.state.vfo_a_hz == 14_074_000
        assert self.radio.state.vfo_b_hz == 7_074_000

    @pytest.mark.asyncio
    async def test_dispatch_mode_reply_updates_state(self):
        self.radio._dispatch_response(b"MD02;")
        assert self.radio.state.mode == Mode.USB


class TestFTdx10:
    def setup_method(self):
        self.radio = FTdx10()

    def test_set_freq_same_as_ft710(self):
        assert self.radio.build_set_freq(VFO.A, 21_074_000) == b"FA021074000;"

    def test_parse_freq(self):
        assert self.radio.parse_freq_reply(b"FA021074000;") == 21_074_000


class TestFTdx101:
    def setup_method(self):
        self.radio = FTdx101()

    def test_set_agc(self):
        assert self.radio.build_set_agc("S") == b"GT03;"

    def test_set_preamp(self):
        assert self.radio.build_set_preamp(1) == b"PA01;"

    def test_set_attenuator_on(self):
        assert self.radio.build_set_attenuator(True) == b"RA01;"


class TestBand:
    @pytest.mark.parametrize("hz,expected", [
        (14_074_000, Band.M20),
        (7_074_000, Band.M40),
        (3_573_000, Band.M80),
        (1_840_000, Band.M160),
        (28_074_000, Band.M10),
        (144_200_000, Band.M2),
        (50_313_000, Band.M6),
        (999_999_999, Band.UNKNOWN),
    ])
    def test_from_hz(self, hz, expected):
        assert Band.from_hz(hz) == expected


class TestMode:
    @pytest.mark.parametrize("code,expected", [
        ("1", Mode.LSB),
        ("2", Mode.USB),
        ("3", Mode.CW),
        ("4", Mode.FM),
        ("5", Mode.AM),
        ("C", Mode.DATA_USB),
        ("E", Mode.C4FM),
        ("Z", Mode.UNKNOWN),
    ])
    def test_from_code(self, code, expected):
        assert Mode.from_code(code) == expected
