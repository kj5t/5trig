"""The vectorised sidetone callback must sound exactly like the per-sample loop it replaced."""

import numpy as np

from fivetr.keyer.sidetone import BLOCK_SIZE, SAMPLE_RATE, SidetoneGenerator


def reference_block(gen, keyed, frames):
    """The original per-sample implementation, operating on *gen*'s state."""
    phase_inc = 2.0 * np.pi * gen._freq / SAMPLE_RATE
    out = np.zeros(frames, dtype=np.float32)
    for i in range(frames):
        if keyed:
            gen._envelope = min(1.0, gen._envelope + gen._ramp_step)
        else:
            gen._envelope = max(0.0, gen._envelope - gen._ramp_step)
        out[i] = np.sin(gen._phase) * gen._envelope * gen._volume
        gen._phase += phase_inc
    if gen._phase > 2.0 * np.pi:
        gen._phase -= 2.0 * np.pi
    return out


def run(gen, keyed, frames=BLOCK_SIZE):
    gen._keyed = keyed
    buf = np.zeros((frames, 1), dtype=np.float32)
    gen._audio_callback(buf, frames, None, None)
    return buf[:, 0].copy()


def test_matches_the_per_sample_loop_through_keying_and_release():
    new, old = SidetoneGenerator(freq=700, volume=0.5), SidetoneGenerator(freq=700, volume=0.5)
    pattern = [False] * 3 + [True] * 4 + [False] * 4 + [True, False, True] * 3 + [False] * 4
    for keyed in pattern:
        if new._envelope == 0.0:
            old._phase = new._phase     # silence skips the phase advance (start phase is arbitrary)
        got = run(new, keyed)
        want = reference_block(old, keyed, BLOCK_SIZE)
        assert np.allclose(got, want, atol=1e-5)


def test_ramp_is_a_few_ms_not_a_click():
    gen = SidetoneGenerator(freq=700, volume=1.0)
    out = run(gen, True)
    assert abs(out[0]) < 0.05              # starts near zero...
    assert np.max(np.abs(out)) > 0.9       # ...and reaches full level


def test_idle_is_silent_and_does_no_work():
    gen = SidetoneGenerator()
    phase = gen._phase
    out = run(gen, False)
    assert not out.any() and gen._phase == phase


def test_fades_out_to_silence_after_release():
    gen = SidetoneGenerator()
    run(gen, True)
    run(gen, False)
    assert gen._envelope == 0.0
    assert not run(gen, False).any()


def test_odd_block_sizes_and_frequency_change():
    new, old = SidetoneGenerator(), SidetoneGenerator()
    for keyed, frames in [(True, 100), (True, 37), (False, 5), (True, 480)]:
        new.freq = old.freq = 900
        if new._envelope == 0.0:
            old._phase = new._phase
        assert np.allclose(run(new, keyed, frames), reference_block(old, keyed, frames), atol=1e-5)
