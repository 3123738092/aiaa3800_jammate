import numpy as np
import pytest

from jammate.analysis.tempo import detect_onsets, tempo_from_onsets

SR = 22050


@pytest.mark.unit
def test_steady_claps_give_their_tempo():
    assert tempo_from_onsets([0.0, 0.6, 1.2, 1.8]) == pytest.approx(100.0)


@pytest.mark.unit
def test_slow_claps_are_folded_into_range():
    # 1.5 s apart = 40 BPM; people often clap half-time, so read it as 80.
    assert tempo_from_onsets([0.0, 1.5, 3.0, 4.5]) == pytest.approx(80.0)


@pytest.mark.unit
@pytest.mark.parametrize("onsets", [[], [0.0, 0.5], [0.0, 0.3, 1.4, 1.6, 3.0]])
def test_too_few_or_irregular_claps_give_no_tempo(onsets):
    assert tempo_from_onsets(onsets) is None


@pytest.mark.integration
def test_detect_onsets_finds_synthetic_claps():
    rng = np.random.default_rng(0)
    y = np.zeros(int(3.0 * SR), dtype=np.float32)
    clap_times = [0.5, 1.1, 1.7, 2.3]
    for t in clap_times:
        i = int(t * SR)
        burst = rng.standard_normal(int(0.03 * SR)) * np.exp(-np.linspace(0, 6, int(0.03 * SR)))
        y[i:i + burst.size] += 0.8 * burst.astype(np.float32)
    found = detect_onsets(y, SR)
    assert len(found) == len(clap_times)
    assert np.allclose(found, clap_times, atol=0.035)


@pytest.mark.unit
def test_impossible_bpm_range_is_rejected_instead_of_looping():
    with pytest.raises(ValueError):
        tempo_from_onsets([0.0, 0.66, 1.33, 2.0], min_bpm=100.0, max_bpm=150.0)
