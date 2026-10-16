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
