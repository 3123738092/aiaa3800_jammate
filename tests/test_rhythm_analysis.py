import numpy as np
import pytest

from jammate.analysis.rhythm import analyze_rhythm_audio, analyze_rhythm_bar, rhythm_from_onsets

SR = 22050


def claps(times, length_s, accents=None):
    audio = np.zeros(round(length_s * SR), dtype=np.float32)
    rng = np.random.default_rng(7)
    for time, accent in zip(times, accents if accents is not None else [1] * len(times)):
        start = round(time * SR)
        size = min(round(.03 * SR), len(audio) - start)
        burst = rng.standard_normal(size) * np.exp(-np.linspace(0, 6, size)) * .25 * accent
        audio[start:start + size] += burst.astype(np.float32)
    return audio


@pytest.mark.unit
def test_quantization_preserves_leading_rest_merges_duplicates_and_excludes_other_bars():
    context = rhythm_from_onsets([.5, 1.25, 1.26, 2.5, 3.5, 4.0], 120, 1,
                                 [.5, .5, 1, .8, .5, .5])
    assert context.onset_beats == (.5, 3)
    assert context.accents == (1, .8)
    assert context.confidence > .9


@pytest.mark.integration
@pytest.mark.parametrize("tempo", [80, 100, 137])
def test_count_in_plus_syncopated_claps_recovers_the_pattern_and_accents(tempo):
    period = 60 / tempo
    origin = .3 + 4 * period
    beats = (0, 1, 1.5, 3)
    times = [.3 + i * period for i in range(4)] + [origin + b * period for b in beats]
    audio = claps(times, origin + 4 * period + .05, [1] * 4 + [1, .4, .8, 1])
    context = analyze_rhythm_audio(audio, SR)
    assert context.tempo_bpm == pytest.approx(tempo, abs=2)
    assert context.beat_origin_s == pytest.approx(origin, abs=.04)
    assert context.onset_beats == beats
    assert context.accents[0] > context.accents[1]
    assert context.confidence >= .65


@pytest.mark.integration
def test_known_tempo_and_explicit_origin_preserve_a_leading_rest():
    audio = claps([.5, .75, 1.5], 2.1)
    context = analyze_rhythm_audio(audio, SR, tempo_hint=120, count_in=0, beat_origin_s=0)
    assert context.onset_beats == (1, 1.5, 3)


@pytest.mark.integration
def test_live_bar_handles_an_attack_at_sample_zero():
    context = analyze_rhythm_bar(claps([0, .5, .75, 1.5], 2), SR, 120, 10)
    assert context.onset_beats == (0, 1, 1.5, 3)
    assert context.beat_origin_s == 10


@pytest.mark.integration
def test_silence_has_zero_confidence_and_incomplete_recordings_are_rejected():
    context = analyze_rhythm_bar(np.zeros(SR * 2), SR, 120)
    assert context.onset_beats == () and context.confidence == 0
    with pytest.raises(ValueError, match="complete rhythm bar"):
        analyze_rhythm_audio(claps([.1, .6], 1), SR, 120, count_in=0, beat_origin_s=0)


@pytest.mark.unit
def test_count_in_free_audio_requires_explicit_tempo():
    with pytest.raises(ValueError, match="tempo_hint"):
        analyze_rhythm_audio(np.zeros(SR), SR, count_in=0)
