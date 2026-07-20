"""Tests for POTA spot parsing."""

import pytest
from fivetr.pota.spot import PotaSpot

_RAW_CW = {
    "spotId": 53750386,
    "activator": "KC4YRV",
    "frequency": "14065.0",
    "mode": "CW",
    "reference": "US-2177",
    "parkName": None,
    "spotTime": "2026-07-20T17:30:07",
    "spotter": "W8WWV-#",
    "comments": "RBN 21 dB 18 WPM via W8WWV-#",
    "name": "Fort Yargo State Park",
    "locationDesc": "US-GA",
}

_RAW_SSB = {
    "spotId": 53749593,
    "activator": "KA5D",
    "frequency": "14244",
    "mode": "SSB",
    "reference": "US-2703",
    "parkName": None,
    "spotTime": "2026-07-20T17:00:27",
    "spotter": "KA5D",
    "comments": "Qrt",
    "name": "Living Desert State Park",
    "locationDesc": "US-NM",
}


class TestPotaSpot:
    def test_from_json_cw(self):
        spot = PotaSpot.from_json(_RAW_CW)
        assert spot is not None
        assert spot.activator == "KC4YRV"
        assert spot.reference == "US-2177"
        assert spot.park_name == "Fort Yargo State Park"
        assert spot.location == "US-GA"
        assert spot.freq_khz == pytest.approx(14065.0)
        assert spot.mode == "CW"
        assert spot.band == "20m"
        assert spot.time_hhmm == "17:30"

    def test_from_json_ssb(self):
        spot = PotaSpot.from_json(_RAW_SSB)
        assert spot is not None
        assert spot.mode == "SSB"
        assert spot.band == "20m"

    def test_freq_mhz(self):
        spot = PotaSpot.from_json(_RAW_CW)
        assert spot.freq_mhz == pytest.approx(14.065)

    def test_missing_reference_returns_none(self):
        raw = dict(_RAW_CW)
        del raw["reference"]
        assert PotaSpot.from_json(raw) is None

    def test_missing_activator_returns_none(self):
        raw = dict(_RAW_CW)
        raw["activator"] = ""
        assert PotaSpot.from_json(raw) is None

    def test_bad_frequency_returns_none(self):
        raw = dict(_RAW_CW)
        raw["frequency"] = "not-a-number"
        assert PotaSpot.from_json(raw) is None

    def test_falls_back_to_park_name_field(self):
        raw = dict(_RAW_CW)
        raw["name"] = None
        raw["parkName"] = "Fallback Park"
        spot = PotaSpot.from_json(raw)
        assert spot.park_name == "Fallback Park"

    def test_time_hhmm_missing(self):
        raw = dict(_RAW_CW)
        raw["spotTime"] = ""
        spot = PotaSpot.from_json(raw)
        assert spot.time_hhmm == ""
