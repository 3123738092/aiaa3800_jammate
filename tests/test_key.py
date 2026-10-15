import pytest

from jammate.analysis.key import estimate_key, pitch_class_histogram
from jammate.music import Key, Note

pytestmark = pytest.mark.unit


def _line(pitches_and_beats):
    notes, t = [], 0.0
    for pitch, beats in pitches_and_beats:
        notes.append(Note(pitch, t, beats))
        t += beats
    return tuple(notes)


def test_histogram_is_weighted_by_duration():
    hist = pitch_class_histogram(_line([(60, 2.0), (72, 1.0), (64, 0.5)]))
    assert hist[0] == pytest.approx(3.0)
    assert hist[4] == pytest.approx(0.5)


def test_major_scale_tune_is_major():
    tune = _line([(60, 1), (62, 0.5), (64, 0.5), (65, 1), (67, 2), (64, 1), (60, 2)])
    estimate = estimate_key(tune)
    assert estimate is not None
    assert estimate.key == Key(0, "major")
    assert estimate.margin > 0


def test_minor_tune_with_leading_tone_is_minor():
    tune = _line([(69, 2), (72, 1), (76, 1), (68, 0.5), (69, 2), (71, 0.5), (72, 0.5), (76, 1)])
    estimate = estimate_key(tune)
    assert estimate is not None and estimate.key == Key(9, "minor")


def test_too_little_material_gives_no_estimate():
    assert estimate_key(()) is None
    assert estimate_key(_line([(60, 1), (67, 1)])) is None


def test_flat_histogram_has_no_key():
    chromatic = _line([(60 + i, 1.0) for i in range(12)])
    assert estimate_key(chromatic) is None
