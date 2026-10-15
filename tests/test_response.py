import random

import pytest

from jammate.generate.harmony import triad
from jammate.generate.response import (
    LEAD_HIGH,
    LEAD_LOW,
    TRANSFORMS,
    answer_motif,
    initiate_motif,
    normalize,
)
from jammate.music import Key, Note

pytestmark = pytest.mark.unit

C_MAJOR = Key(0, "major")
# A rising hum with one slightly sharp note (C#) as people really sing.
MOTIF = (
    Note(60, 2.0, 1.0, instrument="user"),
    Note(62, 3.0, 0.5, instrument="user"),
    Note(61, 3.5, 0.5, instrument="user"),
    Note(67, 4.0, 1.0, instrument="user"),
)


def _check_answer(notes, key):
    tonic_triad = set(triad(key, 0).pcs)
    assert notes, "answer must not be empty"
    assert all(key.contains(n.pitch) for n in notes)
    assert all(LEAD_LOW <= n.pitch <= LEAD_HIGH for n in notes)
    assert notes[-1].pitch % 12 in tonic_triad, "answer should resolve to the tonic chord"
    assert all(n.instrument == "lead" for n in notes)
    assert notes[0].start == 0.0


def test_normalize_moves_motif_to_zero_and_sorts():
    shuffled = (MOTIF[2], MOTIF[0], MOTIF[3], MOTIF[1])
    out = normalize(shuffled)
    assert [n.start for n in out] == [0.0, 1.0, 1.5, 2.0]
    assert normalize(()) == ()


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_every_transform_produces_a_valid_answer(transform):
    answer = answer_motif(MOTIF, C_MAJOR, random.Random(0), transform=transform)
    assert answer.transform == transform
    _check_answer(answer.notes, C_MAJOR)


def test_echo_keeps_the_rhythm():
    answer = answer_motif(MOTIF, C_MAJOR, random.Random(0), transform="echo")
    assert [(n.start, n.duration) for n in answer.notes] == [
        (0.0, 1.0), (1.0, 0.5), (1.5, 0.5), (2.0, 1.0)]


def test_inversion_flips_the_contour():
    rising = (Note(60, 0, 1), Note(62, 1, 1), Note(64, 2, 1), Note(65, 3, 1))
    answer = answer_motif(rising, C_MAJOR, random.Random(0), transform="inversion")
    pitches = [n.pitch for n in answer.notes]
    assert pitches[1] < pitches[0] and pitches[2] < pitches[1]


def test_retrograde_reverses_pitch_order():
    line = (Note(60, 0, 1), Note(64, 1, 1), Note(67, 2, 1), Note(72, 3, 1))
    answer = answer_motif(line, C_MAJOR, random.Random(0), transform="retrograde")
    assert [n.pitch for n in answer.notes][:3] == [72, 67, 64]


@pytest.mark.parametrize("seed", range(10))
def test_random_answer_never_parrots_the_user(seed):
    palindrome = tuple(Note(p, float(i), 1.0) for i, p in enumerate((64, 66, 68, 71, 68, 66, 64)))
    answer = answer_motif(palindrome, Key(4, "major"), random.Random(seed))
    assert [n.pitch for n in answer.notes] != [n.pitch for n in palindrome]
    assert answer.transform != "echo"


def test_answer_is_deterministic_for_a_seed():
    a = answer_motif(MOTIF, C_MAJOR, random.Random(42))
    b = answer_motif(MOTIF, C_MAJOR, random.Random(42))
    assert a == b


def test_unknown_transform_and_empty_motif():
    with pytest.raises(ValueError):
        answer_motif(MOTIF, C_MAJOR, random.Random(0), transform="shuffle")
    empty = answer_motif((), C_MAJOR, random.Random(0))
    assert empty.notes == () and empty.transform == "none"


@pytest.mark.parametrize("seed", range(5))
def test_initiated_motif_is_short_singable_and_resolves(seed):
    key = Key(9, "minor")
    notes = initiate_motif(key, random.Random(seed))
    _check_answer(notes, key)
    assert len(notes) >= 3
    assert max(n.end for n in notes) <= 4.0 + 1e-9
    leaps = [abs(b.pitch - a.pitch) for a, b in zip(notes, notes[1:])]
    assert max(leaps) <= 7, "an invitation motif should be easy to sing back"
