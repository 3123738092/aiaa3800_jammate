import json

import pytest

from jammate.rhythm import RhythmContext, load_rhythm

pytestmark = pytest.mark.unit


def test_context_accepts_json_lists_but_keeps_immutable_tuples(tmp_path):
    data = dict(tempo_bpm=100, onset_beats=[0, 1, 1.5, 3], accents=[1, .5, .8, 1])
    path = tmp_path / "rhythm.json"
    path.write_text(json.dumps(data))
    rhythm = load_rhythm(path)
    data["onset_beats"].append(3.5)
    assert rhythm.onset_beats == (0, 1, 1.5, 3)
    assert RhythmContext(**json.loads(json.dumps(rhythm.to_dict()))) == rhythm


@pytest.mark.parametrize("changes", [
    {"tempo_bpm": float("nan")}, {"tempo_bpm": 0}, {"tempo_bpm": 200},
    {"beat_origin_s": -1}, {"beat_origin_s": float("inf")},
    {"onset_beats": (1, 0)}, {"onset_beats": (0, 0)},
    {"onset_beats": (0, 4)}, {"onset_beats": (0, float("nan"))},
    {"accents": (1,)}, {"accents": (0, 1)}, {"accents": (1, float("inf"))},
    {"beats_per_bar": 3}, {"confidence": -1}, {"confidence": float("nan")},
])
def test_invalid_rhythm_cannot_enter_the_music_state(changes):
    data = dict(tempo_bpm=100, onset_beats=(0, 1), accents=(1, .7))
    with pytest.raises(ValueError):
        RhythmContext(**(data | changes))


@pytest.mark.parametrize("data", [[], {"tempo_bpm": "fast"},
                                  {"tempo_bpm": 100, "unknown": 1}])
def test_bad_json_schema_is_reported_as_value_error(tmp_path, data):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_rhythm(path)
