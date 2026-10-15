import numpy as np
import pytest

from jammate.music import Note
from jammate.render.synth import SR, mix, render

pytestmark = pytest.mark.unit


def _first_sound(audio, threshold=1e-3):
    return int(np.flatnonzero(np.abs(audio) > threshold)[0])


def test_empty_render_is_silence_of_requested_length():
    audio = render((), tempo_bpm=120.0, total_s=1.0)
    assert audio.shape == (SR,) and not audio.any()


def test_notes_land_at_their_beat_time():
    audio = render((Note(69, 2.0, 1.0),), tempo_bpm=120.0)  # beat 2 at 120 BPM = 1.0 s
    assert _first_sound(audio) == pytest.approx(SR * 1.0, abs=SR * 0.01)


def test_offset_moves_the_whole_band():
    audio = render((Note(69, 0.0, 1.0),), tempo_bpm=120.0, offset_s=0.5)
    assert _first_sound(audio) == pytest.approx(SR * 0.5, abs=SR * 0.01)


@pytest.mark.parametrize("instrument,pitch", [
    ("drums", 36), ("drums", 38), ("drums", 42), ("drums", 46), ("drums", 49), ("drums", 77),
    ("bass", 40), ("keys", 60), ("lead", 72), ("user", 64),
])
def test_every_voice_renders_finite_bounded_audio(instrument, pitch):
    audio = render((Note(pitch, 0.0, 1.0, instrument=instrument),), tempo_bpm=100.0)
    assert audio.dtype == np.float32
    assert np.isfinite(audio).all() and np.abs(audio).max() > 0.01
    assert np.abs(audio).max() <= 0.99


def test_dense_mix_is_normalized():
    chord = tuple(Note(p, 0.0, 2.0, velocity=127, instrument="keys") for p in range(48, 84, 2))
    audio = render(chord, tempo_bpm=100.0)
    assert np.abs(audio).max() <= 0.99


def test_mix_pads_to_longest_track():
    out = mix(np.ones(10, dtype=np.float32) * 0.1, np.ones(4, dtype=np.float32) * 0.1)
    assert out.shape == (10,)
    assert out[0] == pytest.approx(0.2) and out[-1] == pytest.approx(0.1)
