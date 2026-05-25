"""
Tests for the CW keyer subsystem.

All tests use the IambicKeyer directly (no radio, no MIDI hardware) so
they run without any attached devices.
"""
from __future__ import annotations

import asyncio
import pytest

from fivetr.keyer.iambic import IambicKeyer, KeyerMode
from fivetr.keyer.midi_input import MidiInput


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

def _wpm_unit(wpm: int) -> float:
    """One dit-unit in seconds for the given WPM."""
    return 1.2 / wpm


# ---------------------------------------------------------------------------
# Straight key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_straight_key_follows_paddle():
    """Straight key mode: set_dit(True) fires callback immediately."""
    events: list[bool] = []
    keyer = IambicKeyer(key_callback=events.append, wpm=30, mode=KeyerMode.STRAIGHT)
    await keyer.start()

    keyer.set_dit(True)
    await asyncio.sleep(0.02)
    keyer.set_dit(False)
    await asyncio.sleep(0.02)

    await keyer.stop()
    assert True in events
    assert False in events


@pytest.mark.asyncio
async def test_straight_dah_also_keys():
    """Straight key: set_dah acts like set_dit (both fire the key)."""
    events: list[bool] = []
    keyer = IambicKeyer(key_callback=events.append, wpm=30, mode=KeyerMode.STRAIGHT)
    await keyer.start()

    keyer.set_dah(True)
    await asyncio.sleep(0.02)
    keyer.set_dah(False)
    await asyncio.sleep(0.02)

    await keyer.stop()
    assert True in events


# ---------------------------------------------------------------------------
# Iambic dit / dah timing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_dit_timing():
    """Single dit: key down for 1 unit, then up."""
    events: list[tuple[bool, float]] = []
    t0 = asyncio.get_event_loop().time()

    def cb(down: bool) -> None:
        events.append((down, asyncio.get_event_loop().time() - t0))

    wpm = 40
    unit = _wpm_unit(wpm)
    keyer = IambicKeyer(key_callback=cb, wpm=wpm, mode=KeyerMode.IAMBIC_A)
    await keyer.start()

    keyer.set_dit(True)
    await asyncio.sleep(unit * 0.6)  # release before dah window
    keyer.set_dit(False)
    await asyncio.sleep(unit * 2.5)  # wait for element + gap

    await keyer.stop()

    downs = [e for e in events if e[0] is True]
    ups = [e for e in events if e[0] is False]
    assert downs, "expected at least one key-down"
    assert ups, "expected at least one key-up"
    # Duration of first element should be ~1 unit (±50% tolerance for CI jitter)
    if len(downs) >= 1 and len(ups) >= 1:
        duration = ups[0][1] - downs[0][1]
        assert 0.4 * unit < duration < 2.0 * unit, (
            f"dit duration {duration*1000:.1f} ms expected ~{unit*1000:.1f} ms"
        )


@pytest.mark.asyncio
async def test_dah_is_three_times_dit():
    """Single dah: key down for ~3 units."""
    events: list[tuple[bool, float]] = []
    t0 = asyncio.get_event_loop().time()

    def cb(down: bool) -> None:
        events.append((down, asyncio.get_event_loop().time() - t0))

    wpm = 40
    unit = _wpm_unit(wpm)
    keyer = IambicKeyer(key_callback=cb, wpm=wpm, mode=KeyerMode.IAMBIC_A)
    await keyer.start()

    keyer.set_dah(True)
    await asyncio.sleep(unit * 0.5)
    keyer.set_dah(False)
    await asyncio.sleep(unit * 5.0)

    await keyer.stop()

    downs = [e for e in events if e[0] is True]
    ups = [e for e in events if e[0] is False]
    assert downs and ups
    duration = ups[0][1] - downs[0][1]
    assert 1.5 * unit < duration < 5.5 * unit, (
        f"dah duration {duration*1000:.1f} ms expected ~{3*unit*1000:.1f} ms"
    )


# ---------------------------------------------------------------------------
# Iambic squeeze
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_squeeze_alternates_dit_dah():
    """Squeeze both paddles: get alternating dit-dah-dit-dah sequence."""
    events: list[bool] = []
    keyer = IambicKeyer(key_callback=events.append, wpm=40, mode=KeyerMode.IAMBIC_A)
    await keyer.start()

    unit = _wpm_unit(40)
    keyer.set_dit(True)
    keyer.set_dah(True)
    await asyncio.sleep(unit * 12)    # 3 dit-dah pairs @ 40 WPM
    keyer.set_dit(False)
    keyer.set_dah(False)
    await asyncio.sleep(unit * 2)

    await keyer.stop()

    # Should have multiple key-down / key-up transitions
    key_downs = events.count(True)
    assert key_downs >= 3, f"expected ≥3 elements in squeeze, got {key_downs}"


# ---------------------------------------------------------------------------
# Mode B extra element
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_b_inserts_extra_element():
    """Mode B: releasing squeeze during element inserts one extra element."""
    events: list[bool] = []
    keyer = IambicKeyer(key_callback=events.append, wpm=40, mode=KeyerMode.IAMBIC_B)
    await keyer.start()

    unit = _wpm_unit(40)
    # Press dit first, then add dah DURING the dit element
    keyer.set_dit(True)
    await asyncio.sleep(unit * 0.3)   # still inside dit
    keyer.set_dah(True)               # squeeze
    await asyncio.sleep(unit * 0.4)   # still inside dit
    keyer.set_dit(False)
    keyer.set_dah(False)              # release both during dit
    await asyncio.sleep(unit * 8)     # wait for extra element

    await keyer.stop()

    # Mode B should produce at least 2 elements (dit + extra dah)
    key_downs = events.count(True)
    assert key_downs >= 2, f"Mode B: expected extra element, got {key_downs} key-downs"


# ---------------------------------------------------------------------------
# MIDI port listing
# ---------------------------------------------------------------------------

def test_midi_list_ports_returns_list():
    """list_ports() returns a list (may be empty if no MIDI hardware)."""
    ports = MidiInput.list_ports()
    assert isinstance(ports, list)


def test_midi_probe_vail_returns_none_or_str():
    """probe_vail() returns None or a port name string."""
    result = MidiInput.probe_vail()
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Keyer stop releases key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_releases_key():
    """Stopping the keyer while a paddle is held should send key-up."""
    events: list[bool] = []
    keyer = IambicKeyer(key_callback=events.append, wpm=20, mode=KeyerMode.IAMBIC_A)
    await keyer.start()

    keyer.set_dah(True)
    await asyncio.sleep(0.1)  # let it key
    await keyer.stop()

    # Last event must be key-up (False)
    if events:
        assert events[-1] is False, "keyer stop did not release the key"
