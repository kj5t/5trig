"""Remote control of the radio's receive filter (width, NAR, IF shift)."""

import asyncio
import os

import pytest

from fivetr.cat.base import CATRadio, Mode, RadioState
from fivetr.cat.filters import widths_for_mode
from fivetr.cat.ftdx10 import FTdx10
from fivetr.remote.client import RemoteRadio
from fivetr.remote.server import RemoteServer


class RecordingFTdx10(FTdx10):
    def __init__(self):
        super().__init__()
        self.sent: list[bytes] = []

    def enqueue(self, cmd):
        self.sent.append(cmd.raw)


async def until(cond, timeout=3.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


class TestWidthTables:
    def test_ssb_table_matches_the_manual(self):
        t = widths_for_mode(Mode.USB)
        assert (t[0], t[2], t[-1], len(t)) == (300, 600, 4000, 23)

    def test_cw_data_and_rtty_share_the_narrow_table(self):
        t = widths_for_mode(Mode.CW)
        assert (t[0], t[-1], len(t)) == (50, 3000, 18)
        assert widths_for_mode(Mode.DATA_USB) == t == widths_for_mode(Mode.RTTY_LSB)

    def test_modes_the_radio_has_no_width_table_for(self):
        assert widths_for_mode(Mode.FM) == []
        assert widths_for_mode(Mode.AM) == []


class TestFTdx10Commands:
    def test_set_width(self):
        assert FTdx10().build_set_filter_width(0) == b"SH0000;"
        assert FTdx10().build_set_filter_width(9) == b"SH0009;"
        assert FTdx10().build_set_filter_width(23) == b"SH0023;"

    def test_set_if_shift_sign_and_padding(self):
        r = FTdx10()
        assert r.build_set_if_shift(0) == b"IS00+0000;"
        assert r.build_set_if_shift(200) == b"IS00+0200;"
        assert r.build_set_if_shift(-1200) == b"IS00-1200;"

    def test_if_shift_is_clamped_and_snapped_to_20_hz(self):
        r = FTdx10()
        assert r.build_set_if_shift(5000) == b"IS00+1200;"
        assert r.build_set_if_shift(-5000) == b"IS00-1200;"
        assert r.build_set_if_shift(190) == b"IS00+0200;"

    def test_narrow(self):
        r = FTdx10()
        assert r.build_set_narrow(True) == b"NA01;"
        assert r.build_set_narrow(False) == b"NA00;"

    def test_setters_queue_the_command_and_update_state(self):
        r = RecordingFTdx10()
        r.set_filter_width(7)
        r.set_if_shift(-300)
        r.set_narrow(True)
        assert r.sent == [b"SH0007;", b"IS00-0300;", b"NA01;"]
        assert (r.state.filter_width_idx, r.state.if_shift_hz, r.state.narrow) == (7, -300, True)

    def test_setting_width_clamps_the_index(self):
        r = RecordingFTdx10()
        r.set_filter_width(99)
        assert r.sent == [b"SH0023;"]

    def test_slow_poll_asks_for_all_three(self):
        raws = [c.raw for c in FTdx10().build_slow_poll_commands()]
        assert raws == [b"SH0;", b"IS0;", b"NA0;"]


class TestFTdx10Replies:
    async def test_replies_update_state(self):
        r = FTdx10()
        r._dispatch_response(b"SH0012;IS00-0340;NA01;")
        assert r.state.filter_width_idx == 12
        assert r.state.if_shift_hz == -340
        assert r.state.narrow is True
        r._dispatch_response(b"IS00+0100;NA00;")
        assert (r.state.if_shift_hz, r.state.narrow) == (100, False)

    async def test_garbled_reply_is_ignored(self):
        r = FTdx10()
        r._dispatch_response(b"SH0xx;")
        assert r.state.filter_width_idx is None


class TestRadiosWithoutFilterControl:
    def test_default_setters_do_nothing_and_state_stays_unknown(self):
        class Plain(CATRadio):
            pass
        r = Plain()
        r.set_filter_width(3)
        r.set_if_shift(100)
        r.set_narrow(True)
        assert r.build_slow_poll_commands() == []
        s = r.state
        assert (s.filter_width_idx, s.if_shift_hz, s.narrow) == (None, None, None)


class TestRemote:
    async def test_commands_reach_the_shack_radio_and_state_comes_back(self):
        radio = RecordingFTdx10()
        server = RemoteServer(radio, host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        client = RemoteRadio("127.0.0.1", port)
        try:
            await client.connect()
            client.set_filter_width(5)
            client.set_if_shift(-200)
            client.set_narrow(True)
            assert await until(lambda: len(radio.sent) == 3)
            assert radio.sent == [b"SH0005;", b"IS00-0200;", b"NA01;"]
            await server.on_state_changed(radio.state.clone())
            assert await until(lambda: client.state.filter_width_idx == 5)
            assert (client.state.if_shift_hz, client.state.narrow) == (-200, True)
        finally:
            await client.disconnect()
            await server.stop()

    async def test_radio_without_filter_reports_unknown_to_the_client(self):
        class Plain(CATRadio):
            MODEL_NAME = "Plain"
        server = RemoteServer(Plain(), host="127.0.0.1", port=0, audio_port=None)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        client = RemoteRadio("127.0.0.1", port)
        try:
            await client.connect()
            await server.on_state_changed(RadioState())
            await asyncio.sleep(0.2)
            s = client.state
            assert (s.filter_width_idx, s.if_shift_hz, s.narrow) == (None, None, None)
        finally:
            await client.disconnect()
            await server.stop()


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _state(mode=Mode.CW, width=6, shift=0, narrow=False):
    s = RadioState()
    s.mode, s.filter_width_idx, s.if_shift_hz, s.narrow = mode, width, shift, narrow
    return s


class TestFilterPanel:
    def test_hidden_until_the_radio_reports_a_filter(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        p.update_state(RadioState())
        assert not p.isVisibleTo(p) or p.isHidden()
        p.update_state(_state())
        assert not p.isHidden()
        p.update_state(RadioState())
        assert p.isHidden()

    def test_width_list_follows_the_mode(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        p.update_state(_state(Mode.CW, width=10))
        assert p._width.count() == 19                       # Default + 18
        assert p._width.currentText() == "500 Hz"           # SH index 10 in CW
        p.update_state(_state(Mode.USB, width=3))
        assert p._width.count() == 24
        assert p._width.currentText() == "600 Hz"
        p.update_state(_state(Mode.FM, width=0))
        assert not p._width.isEnabled()

    def test_radio_updates_do_not_look_like_user_changes(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        seen = []
        p.width_selected.connect(lambda i: seen.append(("w", i)))
        p.narrow_toggled.connect(lambda b: seen.append(("n", b)))
        p.shift_changed.connect(lambda h: seen.append(("s", h)))
        p.update_state(_state(width=4, shift=-200, narrow=True))
        p._send_timer.stop()
        assert seen == []
        assert p._narrow.isChecked() and p._shift.value() == -10

    def test_user_actions_emit(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        p.update_state(_state())
        got = []
        p.width_selected.connect(got.append)
        p.narrow_toggled.connect(got.append)
        p._width.setCurrentIndex(3)
        p._width.activated.emit(3)
        p._narrow.click()
        assert got == [3, True]

    def test_shift_drag_is_debounced_into_one_command(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        p.update_state(_state())
        got = []
        p.shift_changed.connect(got.append)
        for v in (1, 2, 3, 4):
            p._shift.setValue(v)
        assert got == []
        p._send_shift()
        assert got == [80]

    def test_a_stale_radio_value_does_not_undo_a_fresh_user_change(self, qapp):
        from fivetr.ui.filter_panel import FilterPanel
        p = FilterPanel()
        p.update_state(_state(width=6))
        p._width.setCurrentIndex(9)
        p._width.activated.emit(9)
        p.update_state(_state(width=6))                      # radio hasn't caught up yet
        assert p._width.currentData() == 9
