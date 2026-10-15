import math

import pytest

from jammate.music import Chord, Key, Note, hz_to_midi, midi_to_hz

pytestmark = pytest.mark.unit


def test_note_end_and_shift_return_new_note():
    note = Note(pitch=60, start=1.0, duration=0.5)
    moved = note.shifted(4.0)
    assert note.end == 1.5
    assert moved.start == 5.0 and moved.pitch == 60
    assert note.start == 1.0  # original untouched


def test_note_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        Note(pitch=60, start=0.0, duration=0.0)


@pytest.mark.parametrize("tonic,mode,expected", [
    (0, "major", (0, 2, 4, 5, 7, 9, 11)),
    (9, "minor", (9, 11, 0, 2, 4, 5, 7)),
])
def test_scale_pitch_classes(tonic, mode, expected):
    assert Key(tonic, mode).scale_pcs == expected


def test_key_validates_inputs():
    with pytest.raises(ValueError):
        Key(12, "major")
    with pytest.raises(ValueError):
        Key(0, "dorian")


def test_key_name():
    assert Key(7, "major").name == "G major"
    assert Key(9, "minor").name == "A minor"


def test_snap_keeps_scale_tones_and_moves_others_down_first():
    c_major = Key(0, "major")
    assert c_major.snap(64) == 64
    assert c_major.snap(61) == 60  # C# -> C (tie broken downwards)
    assert c_major.snap(66) == 65  # F# -> F


def test_diatonic_index_round_trip():
    a_minor = Key(9, "minor")
    for pitch in (57, 59, 60, 62, 64, 65, 67, 69):
        assert a_minor.from_index(a_minor.to_index(pitch)) == pitch


def test_transpose_diatonic_stays_in_key():
    c_major = Key(0, "major")
    assert c_major.transpose_diatonic(60, 2) == 64
    assert c_major.transpose_diatonic(64, 1) == 65
    assert c_major.transpose_diatonic(60, -1) == 59
    assert c_major.transpose_diatonic(60, 7) == 72


def test_chord_pitch_classes_and_voicing():
    a_minor = Chord(9, "min")
    assert a_minor.pcs == (9, 0, 4)
    assert a_minor.voicing(low=55) == (57, 60, 64)
    assert a_minor.name == "Am"
    assert Chord(11, "dim").name == "Bdim"


def test_chord_rejects_unknown_quality():
    with pytest.raises(ValueError):
        Chord(0, "sus9")


def test_hz_midi_round_trip():
    assert hz_to_midi(440.0) == pytest.approx(69.0)
    assert midi_to_hz(60) == pytest.approx(261.6256, rel=1e-4)
    assert hz_to_midi(midi_to_hz(55.5)) == pytest.approx(55.5)
    assert math.isnan(hz_to_midi(0.0))
