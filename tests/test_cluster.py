"""Tests for DX cluster spot parsing."""

import pytest
from fivetr.cluster.spot import DXSpot, _freq_to_band, _guess_mode


class TestDXSpot:
    def test_parse_ft8_spot(self):
        line = "DX de W6ABC:      14074.0  VK2XYZ       FT8 -12 dB            1234Z"
        spot = DXSpot.parse(line)
        assert spot is not None
        assert spot.dx_call == "VK2XYZ"
        assert spot.spotter == "W6ABC"
        assert spot.freq_khz == pytest.approx(14074.0)
        assert spot.time_utc == "1234"
        assert spot.band == "20m"
        assert spot.mode == "FT8"

    def test_parse_cw_spot(self):
        line = "DX de K1ABC:       7020.0  JA1XYZ       CW up5                0955Z"
        spot = DXSpot.parse(line)
        assert spot is not None
        assert spot.dx_call == "JA1XYZ"
        assert spot.band == "40m"
        assert spot.mode == "CW"

    def test_parse_returns_none_for_garbage(self):
        assert DXSpot.parse("Hello there, just a message") is None

    def test_freq_mhz(self):
        spot = DXSpot(
            spotter="W1AW", dx_call="VE3", freq_khz=14074.0,
            comment="FT8", time_utc="1200"
        )
        assert spot.freq_mhz == pytest.approx(14.074)


class TestFreqToBand:
    @pytest.mark.parametrize("khz,expected", [
        (14074, "20m"),
        (7074, "40m"),
        (21074, "15m"),
        (144200, "2m"),
        (50313, "6m"),
        (999999, "?"),
    ])
    def test_freq_to_band(self, khz, expected):
        assert _freq_to_band(khz) == expected


class TestGuessMode:
    @pytest.mark.parametrize("comment,expected", [
        ("FT8 -15dB", "FT8"),
        ("CW up5", "CW"),
        ("RTTY 45baud", "RTTY"),
        ("psk31", "PSK"),
        ("WSPR -20", "WSPR"),
        ("SSB DX", "SSB"),
        ("just calling", ""),
    ])
    def test_guess_mode(self, comment, expected):
        assert _guess_mode(comment) == expected
