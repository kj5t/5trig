"""RX audio into a named PipeWire sink (for WSJT-X on a remote radio)."""

import subprocess

import numpy as np
import pytest

from fivetr.remote import audio
from fivetr.remote.audio import AudioUDPClient, pipewire_sink_exists


def _fake_pactl(monkeypatch, stdout: str):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    monkeypatch.setattr(audio.subprocess, "run", run)


SINKS = (
    "42\talsa_output.pci-0000\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
    "9\tfivetr_rx\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
)


class TestSinkLookup:
    def test_finds_exact_name(self, monkeypatch):
        _fake_pactl(monkeypatch, SINKS)
        assert pipewire_sink_exists("fivetr_rx")

    def test_substring_is_not_a_match(self, monkeypatch):
        _fake_pactl(monkeypatch, SINKS)
        assert not pipewire_sink_exists("fivetr")

    def test_no_pactl_means_missing(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("pactl")
        monkeypatch.setattr(audio.subprocess, "run", boom)
        assert not pipewire_sink_exists("fivetr_rx")


class TestClientOutput:
    def test_missing_sink_plays_nothing(self, monkeypatch):
        """Never fall back to the speakers when the target sink is absent."""
        _fake_pactl(monkeypatch, SINKS)
        launched = []
        monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **k: launched.append(a))
        client = AudioUDPClient("127.0.0.1", 9, output_target="does_not_exist")
        client._start_pipewire_output()
        assert launched == []
        assert client._pw_proc is None
        assert "does_not_exist" in client.output_error

    def test_no_error_when_sink_exists(self, monkeypatch):
        _fake_pactl(monkeypatch, SINKS)
        monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **k: None)
        client = AudioUDPClient("127.0.0.1", 9, output_target="fivetr_rx")
        client._running = False
        client._start_pipewire_output()
        assert client.output_error is None

    def test_pw_play_command_targets_the_sink(self, monkeypatch):
        _fake_pactl(monkeypatch, SINKS)
        seen = {}

        class FakeProc:
            stdin = None
            def terminate(self): pass
            def wait(self, timeout=None): pass

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            return FakeProc()

        monkeypatch.setattr(audio.subprocess, "Popen", popen)
        client = AudioUDPClient("127.0.0.1", 9, sample_rate=48000, output_target="fivetr_rx")
        client._running = False     # keep the writer thread from looping
        client._start_pipewire_output()
        cmd = seen["cmd"]
        assert cmd[0] == "pw-play"
        assert cmd[cmd.index("--target") + 1] == "fivetr_rx"
        assert cmd[cmd.index("--channels") + 1] == "1"
        assert cmd[cmd.index("--rate") + 1] == "48000"

    def test_queue_drops_oldest_when_full(self):
        client = AudioUDPClient("127.0.0.1", 9, output_target="x")
        for i in range(audio.MAX_BUFFER_FRAMES + 5):
            client._pw_enqueue(np.full(4, i / 100, dtype=np.float32))
        assert client._pw_queue.qsize() == audio.MAX_BUFFER_FRAMES
        first = np.frombuffer(client._pw_queue.get_nowait(), dtype="<i2")[0]
        assert first == int(0.05 * 32767)     # frames 0-4 were dropped

    def test_samples_are_clipped_s16(self):
        client = AudioUDPClient("127.0.0.1", 9, output_target="x")
        client._pw_enqueue(np.array([2.0, -2.0, 0.0], dtype=np.float32))
        got = np.frombuffer(client._pw_queue.get_nowait(), dtype="<i2")
        assert list(got) == [32767, -32767, 0]


@pytest.mark.skipif(not __import__("shutil").which("pactl"), reason="needs PipeWire/pactl")
def test_default_path_untouched_without_target():
    client = AudioUDPClient("127.0.0.1", 9)
    assert client._output_target == ""
    assert client._pw_proc is None


class TestRouteSwitching:
    """Move audio between the WSJT-X sink and the speakers without restarting."""

    @pytest.fixture
    def rig(self, monkeypatch):
        import sys
        import types

        events = []

        class FakeProc:
            def __init__(self, cmd):
                self.cmd = cmd
                self.stdin = None
                events.append(("pw_start", cmd[cmd.index("--target") + 1]))
            def terminate(self): events.append(("pw_stop",))
            def wait(self, timeout=None): pass
            def kill(self): pass

        class FakeStream:
            def __init__(self, **kw): events.append(("sd_open",))
            def start(self): pass
            def stop(self): events.append(("sd_stop",))
            def close(self): pass

        fake_sd = types.SimpleNamespace(OutputStream=FakeStream)
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        _fake_pactl(monkeypatch, SINKS)
        monkeypatch.setattr(audio.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd))

        def make(route="sink", target="fivetr_rx"):
            c = AudioUDPClient("127.0.0.1", 9, output_target=target, route=route)
            c._running = True          # skip the network; exercise the outputs only
            return c
        return make, events

    def test_sink_route_never_opens_the_speakers(self, rig):
        make, events = rig
        c = make("sink")
        c._apply_route()
        assert events == [("pw_start", "fivetr_rx")]
        assert c._stream is None

    def test_switching_to_speakers_and_back(self, rig):
        make, events = rig
        c = make("sink")
        c._apply_route()
        events.clear()
        c.set_route("speakers")
        assert events == [("sd_open",), ("pw_stop",)]          # new output up before old goes
        assert c._pw_proc is None and c._stream is not None
        events.clear()
        c.set_route("sink")
        assert events == [("pw_start", "fivetr_rx"), ("sd_stop",)]
        assert c._stream is None and c._pw_proc is not None

    def test_both_runs_both_and_leaves_running_outputs_alone(self, rig):
        make, events = rig
        c = make("sink")
        c._apply_route()
        events.clear()
        c.set_route("both")
        assert events == [("sd_open",)]                # the sink keeps playing, no gap
        c.set_route("both")
        assert events == [("sd_open",)]                # idempotent
        assert c._pw_proc is not None and c._stream is not None

    def test_without_a_configured_sink_everything_is_speakers(self, rig):
        make, events = rig
        c = make("sink", target="")
        c._apply_route()
        assert events == [("sd_open",)]
        c.set_route("sink")
        assert c._stream is not None and c._pw_proc is None

    def test_missing_sink_stays_silent_rather_than_using_the_speakers(self, rig, monkeypatch):
        make, events = rig
        _fake_pactl(monkeypatch, "9\tsomething_else\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n")
        c = make("sink")
        c._apply_route()
        assert events == [] and c._stream is None
        assert "fivetr_rx" in c.output_error

    def test_speaker_failure_is_reported_not_raised(self, rig, monkeypatch):
        import sys
        make, events = rig

        class Broken:
            def __init__(self, **kw): raise OSError("no device")
        sys.modules["sounddevice"].OutputStream = Broken
        c = make("speakers")
        c._apply_route()
        assert c._stream is None
        assert "no device" in c.output_error

    def test_unknown_route_is_rejected(self, rig):
        make, _ = rig
        with pytest.raises(ValueError):
            make().set_route("headphones")

    def test_buffers_are_flushed_when_the_speakers_reopen(self, rig):
        make, _ = rig
        c = make("sink")
        c._apply_route()
        c._buf.append(np.ones(960, dtype=np.float32))
        c.set_route("speakers")
        assert len(c._buf) == 0
