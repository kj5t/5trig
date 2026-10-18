"""Typing CW on a remote client: the sidetone must follow the dits and dahs.

Regression: the shack's WinKeyer "busy" state (buffer non-empty) came back as a
cw_key_state frame and was treated as key-down, giving one solid tone for as long
as the buffer was busy, while the simulated element timing never reached the sidetone.
"""

import asyncio
from types import SimpleNamespace

from fivetr.remote.client import RemoteRadio
from fivetr.ui.main_window import MainWindow, _generate_cw_events


class FakeSidetone:
    def __init__(self):
        self.log: list[bool] = []

    def key(self, down):
        self.log.append(down)


def stub(wpm=60):
    leds = []
    return SimpleNamespace(
        _sidetone=FakeSidetone(),
        _key_state=SimpleNamespace(emit=leds.append),
        _keyboard_cw_queue=asyncio.Queue(),
        _keyboard_sideline_active=False,
        _config=SimpleNamespace(keyer=SimpleNamespace(wpm=wpm)),
        leds=leds,
    )


def bind(s):
    s._sim_key = lambda d: MainWindow._sim_key(s, d)
    s._schedule_cw_events = lambda ev: MainWindow._schedule_cw_events(s, ev)
    return s


class TestKeyboardSidetone:
    async def test_simulated_elements_key_the_sidetone(self):
        s = bind(stub())
        s._keyboard_sideline_active = True           # as _on_cw_char sets it
        await MainWindow._schedule_cw_events(s, _generate_cw_events("K", 60))   # -.-
        assert s._sidetone.log == [True, False, True, False, True, False]
        assert s.leds == s._sidetone.log

    async def test_flag_clears_when_the_queue_drains_even_for_unsendable_chars(self):
        s = bind(stub())
        task = asyncio.ensure_future(MainWindow._consume_keyboard_cw(s))
        try:
            s._keyboard_sideline_active = True
            s._keyboard_cw_queue.put_nowait("~")      # not a CW character
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not s._keyboard_sideline_active:
                    break
            assert s._keyboard_sideline_active is False
        finally:
            task.cancel()

    async def test_flag_stays_set_between_queued_characters(self):
        s = bind(stub(wpm=60))
        seen = []
        task = asyncio.ensure_future(MainWindow._consume_keyboard_cw(s))
        try:
            s._keyboard_sideline_active = True
            for ch in "ET":
                s._keyboard_cw_queue.put_nowait(ch)
            orig = s._sidetone.key
            s._sidetone.key = lambda d: (seen.append(s._keyboard_sideline_active), orig(d))
            await asyncio.sleep(0.5)
            assert seen and all(seen)                 # guard held through both characters
            assert s._keyboard_sideline_active is False
        finally:
            task.cancel()


class TestBusyIsNotKeyDown:
    async def test_client_ignores_busy_frames(self):
        radio = RemoteRadio("127.0.0.1")
        got = []
        radio.set_key_callback(got.append)
        await radio._handle_msg({"t": "cw_key_state", "down": True, "busy": True})
        await radio._handle_msg({"t": "cw_key_state", "down": False, "busy": True})
        assert got == []

    async def test_real_key_state_still_reaches_the_callback(self):
        radio = RemoteRadio("127.0.0.1")
        got = []
        radio.set_key_callback(got.append)
        await radio._handle_msg({"t": "cw_key_state", "down": True})
        await radio._handle_msg({"t": "cw_key_state", "down": False})
        assert got == [True, False]


class TestConsumerLifecycle:
    async def test_ensure_starts_once_and_restarts_after_cancel(self):
        s = bind(stub())
        s._keyboard_cw_task = None
        s._consume_keyboard_cw = lambda: MainWindow._consume_keyboard_cw(s)
        MainWindow._ensure_keyboard_cw_task(s)
        first = s._keyboard_cw_task
        MainWindow._ensure_keyboard_cw_task(s)
        assert s._keyboard_cw_task is first              # not duplicated
        first.cancel()
        await asyncio.sleep(0)
        MainWindow._ensure_keyboard_cw_task(s)
        assert s._keyboard_cw_task is not first          # a Stop doesn't kill it for good
        s._keyboard_cw_task.cancel()

    async def test_typed_character_is_played_after_a_stop(self):
        s = bind(stub(wpm=60))
        s._radio = object()
        s._keyboard_cw_task = None
        s._consume_keyboard_cw = lambda: MainWindow._consume_keyboard_cw(s)
        s._ensure_keyboard_cw_task = lambda: MainWindow._ensure_keyboard_cw_task(s)
        s._macro_task = None
        s._macro_widget = SimpleNamespace(set_active=lambda a: None)
        s._winkeyer = None
        MainWindow._ensure_keyboard_cw_task(s)
        MainWindow._on_macro_stop(s)
        s._keyboard_cw_queue.put_nowait("E")
        await asyncio.sleep(0.3)
        assert True in s._sidetone.log
        s._keyboard_cw_task.cancel()
