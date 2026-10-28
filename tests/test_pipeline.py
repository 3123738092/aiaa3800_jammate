import numpy as np
import pytest

from jammate.demo import DEMO_MELODY, synth_hum
from jammate.music import BEATS_PER_BAR, Key
from jammate.pipeline import analyze_phrase, jam_offline
from jammate.render.synth import SR
from jammate.state import Action

pytestmark = pytest.mark.integration

TEMPO = 100.0


@pytest.fixture(scope="module")
def hum():
    return synth_hum(DEMO_MELODY, tempo_bpm=TEMPO)


@pytest.fixture(scope="module")
def analysis(hum):
    return analyze_phrase(hum, SR)


def test_count_in_sets_the_tempo(analysis):
    assert analysis.tempo_source == "count-in"
    assert analysis.tempo_bpm == pytest.approx(TEMPO, abs=3.0)


def test_hum_is_transcribed(analysis):
    expected = [pitch for pitch, _ in DEMO_MELODY]
    heard = [n.pitch for n in analysis.notes]
    assert heard == expected
    expected_starts = np.cumsum([0.0] + [beats for _, beats in DEMO_MELODY[:-1]])
    assert np.allclose([n.start for n in analysis.notes], expected_starts, atol=0.25)


def test_jam_answers_in_the_users_key(hum):
    result = jam_offline(hum, SR, bars=8, seed=0)
    first = result.decisions[0]
    assert first.action is Action.FOLLOW
    assert all(first.state.key.contains(p) for p, _ in DEMO_MELODY)
    assert first.state.key != Key(0, "major")  # the demo hums in E, not C


def test_band_only_drums_while_user_sings(hum):
    result = jam_offline(hum, SR, bars=8, seed=0)
    phrase_end = max(n.end for n in result.analysis.notes)
    phrase_bars = int(np.ceil(phrase_end / BEATS_PER_BAR))
    early = [n for n in result.notes if n.start < phrase_bars * BEATS_PER_BAR]
    assert early and {n.instrument for n in early} == {"drums"}


def test_ai_answers_then_invites_after_silence(hum):
    result = jam_offline(hum, SR, bars=8, seed=0)
    actions = [d.action for d in result.decisions]
    assert actions[0] is Action.FOLLOW and Action.INITIATE in actions
    assert any(n.instrument == "lead" for n in result.notes)


def test_audio_outputs_are_bounded_and_aligned(hum):
    result = jam_offline(hum, SR, bars=8, seed=0)
    assert result.mix_audio.shape[0] >= hum.shape[0]
    for audio in (result.band_audio, result.mix_audio):
        assert np.isfinite(audio).all() and np.abs(audio).max() <= 0.99
    assert result.analysis.origin_s == pytest.approx(4 * 60.0 / TEMPO, abs=0.05)


def test_same_seed_same_jam(hum):
    a = jam_offline(hum, SR, bars=6, seed=3)
    b = jam_offline(hum, SR, bars=6, seed=3)
    assert a.notes == b.notes


def test_silence_input_still_grooves_and_invites():
    result = jam_offline(np.zeros(SR * 2, dtype=np.float32), SR, bars=4, seed=0)
    assert result.analysis.notes == () and result.analysis.tempo_source == "default"
    assert result.decisions[0].action is Action.HOLD
    assert any(d.action is Action.INITIATE for d in result.decisions)


def test_out_of_range_tempo_hint_is_clamped_before_quantizing():
    no_count_in = synth_hum(DEMO_MELODY, tempo_bpm=TEMPO, count_in=0)
    fast = analyze_phrase(no_count_in, SR, tempo_hint=300.0)
    assert fast.tempo_source == "hint" and fast.tempo_bpm == 180.0
    result = jam_offline(no_count_in, SR, bars=4, tempo_hint=300.0)
    assert result.decisions[0].state.tempo_bpm == result.analysis.tempo_bpm
