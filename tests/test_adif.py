"""Tests for ADIF log read/write."""

import tempfile
from pathlib import Path

import pytest
from fivetr.logging.adif import ADIFLog, QSORecord


def make_log(tmp_path: Path) -> ADIFLog:
    return ADIFLog(tmp_path / "test.adi")


def test_add_and_retrieve(tmp_path):
    log = make_log(tmp_path)
    qso = QSORecord.now(call="VK2XYZ", freq_khz=14074.0, mode="FT8")
    log.add(qso)
    assert len(log.records) == 1
    assert log.records[0].call == "VK2XYZ"


def test_persistence(tmp_path):
    p = tmp_path / "test.adi"
    log1 = ADIFLog(p)
    log1.add(QSORecord.now("W1AW", 14074.0, "USB"))
    log1.add(QSORecord.now("VE3ABC", 7074.0, "FT8"))

    log2 = ADIFLog(p)   # reload
    assert len(log2.records) == 2
    calls = {r.call for r in log2.records}
    assert "W1AW" in calls
    assert "VE3ABC" in calls


def test_worked_calls(tmp_path):
    log = make_log(tmp_path)
    log.add(QSORecord.now("K1ABC"))
    log.add(QSORecord.now("K1ABC"))   # dupe
    log.add(QSORecord.now("VK2XYZ"))
    worked = log.worked_calls()
    assert worked == {"K1ABC", "VK2XYZ"}


def test_export_adif(tmp_path):
    log = make_log(tmp_path)
    log.add(QSORecord.now("W6ABC", 21074.0, "FT8"))
    dest = tmp_path / "export.adi"
    log.export_adif(dest)
    text = dest.read_text()
    assert "W6ABC" in text
    assert "<CALL:5>W6ABC" in text
    assert "<EOR>" in text


def test_adif_round_trip(tmp_path):
    p = tmp_path / "round_trip.adi"
    log1 = ADIFLog(p)
    q = QSORecord(
        call="DL3ABC",
        freq_khz=14074.0,
        mode="FT8",
        qso_date="20260523",
        time_on="123456",
        rst_sent="59",
        rst_rcvd="59",
        name="Hans",
    )
    log1.add(q)

    log2 = ADIFLog(p)
    r = log2.records[0]
    assert r.call == "DL3ABC"
    assert r.mode == "FT8"
    assert r.name == "Hans"
    assert r.qso_date == "20260523"
