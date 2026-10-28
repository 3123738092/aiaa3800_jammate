import numpy as np
import pytest

from jammate.analysis.vad import VadConfig, VadState, level_db, vad_step

pytestmark = pytest.mark.unit

CONFIG = VadConfig(threshold_db=-35.0, min_phrase_blocks=3, end_silence_blocks=4)


def _run(levels):
    state, phrases = VadState(), []
    for level in levels:
        state, phrase = vad_step(state, level, CONFIG)
        if phrase is not None:
            phrases.append(phrase)
    return state, phrases


def test_level_db_of_silence_and_full_scale():
    assert level_db(np.zeros(256)) < -100
    assert level_db(np.ones(256)) == pytest.approx(0.0)


def test_phrase_is_reported_after_enough_silence():
    loud, quiet = -20.0, -60.0
    _, phrases = _run([quiet] * 2 + [loud] * 5 + [quiet] * 4 + [quiet] * 3)
    assert phrases == [(2, 7)]  # blocks [2, 7) were sung


def test_short_dips_do_not_split_a_phrase():
    loud, quiet = -20.0, -60.0
    _, phrases = _run([loud] * 3 + [quiet] * 2 + [loud] * 3 + [quiet] * 4)
    assert phrases == [(0, 8)]


def test_too_short_sounds_are_ignored():
    loud, quiet = -20.0, -60.0
    state, phrases = _run([loud] * 2 + [quiet] * 6)
    assert phrases == [] and not state.in_phrase


def test_endless_sound_is_cut_into_bounded_phrases():
    config = VadConfig(threshold_db=-35.0, min_phrase_blocks=3, end_silence_blocks=4,
                       max_phrase_blocks=10)
    state, phrases = VadState(), []
    for _ in range(25):
        state, phrase = vad_step(state, -20.0, config)
        if phrase is not None:
            phrases.append(phrase)
    assert phrases == [(0, 10), (10, 20)]
