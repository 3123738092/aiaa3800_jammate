import pytest

from jammate.generate.harmony import progression, triad
from jammate.music import Chord, Key

pytestmark = pytest.mark.unit


def test_triads_follow_the_scale():
    c_major = Key(0, "major")
    assert triad(c_major, 0) == Chord(0, "maj")
    assert triad(c_major, 1) == Chord(2, "min")
    assert triad(c_major, 6) == Chord(11, "dim")
    a_minor = Key(9, "minor")
    assert triad(a_minor, 0) == Chord(9, "min")
    assert triad(a_minor, 2) == Chord(0, "maj")


def test_default_major_progression_is_pop_loop():
    names = [c.name for c in progression(Key(0, "major"), variant=0)]
    assert names == ["C", "G", "Am", "F"]


def test_variants_cycle_and_stay_diatonic():
    key = Key(2, "minor")
    first = progression(key, 0)
    assert progression(key, 3) == first  # three variants per mode, cycling
    for variant in range(3):
        for chord in progression(key, variant):
            assert all(key.contains(pc) for pc in chord.pcs)
