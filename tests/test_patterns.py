import pytest

from jammate.generate.patterns import CRASH, SNARE, bass_bar, comp_bar, drum_bar
from jammate.music import BEATS_PER_BAR, Chord

pytestmark = pytest.mark.unit

A_MINOR = Chord(9, "min")


def _inside_bar(notes):
    return all(0 <= n.start < BEATS_PER_BAR and n.end <= BEATS_PER_BAR + 1e-9 for n in notes)


def test_energy_zero_is_a_break_with_only_a_pad():
    assert drum_bar(energy=0, phrase_pos=0) == ()
    assert bass_bar(A_MINOR, energy=0) == ()
    pad = comp_bar(A_MINOR, energy=0)
    assert pad and {n.pitch % 12 for n in pad} == set(A_MINOR.pcs)


@pytest.mark.parametrize("energy", [1, 2, 3])
def test_parts_stay_inside_one_bar(energy):
    for phrase_pos in range(4):
        assert _inside_bar(drum_bar(energy, phrase_pos))
    assert _inside_bar(bass_bar(A_MINOR, energy))
    assert _inside_bar(comp_bar(A_MINOR, energy))


def test_more_energy_means_more_drum_hits():
    counts = [len(drum_bar(e, phrase_pos=1)) for e in (1, 2, 3)]
    assert counts == sorted(counts) and counts[0] < counts[-1]


def test_crash_marks_phrase_start_at_high_energy():
    assert any(n.pitch == CRASH for n in drum_bar(3, phrase_pos=0))
    assert not any(n.pitch == CRASH for n in drum_bar(3, phrase_pos=1))


def test_fill_on_last_bar_of_phrase():
    fill = drum_bar(2, phrase_pos=3)
    plain = drum_bar(2, phrase_pos=2)
    late_snares = [n for n in fill if n.pitch == SNARE and n.start >= 3.5]
    assert len(late_snares) >= 2
    assert fill != plain


def test_bass_plays_chord_tones_in_bass_register():
    for energy in (1, 2, 3):
        notes = bass_bar(A_MINOR, energy)
        assert notes
        assert all(n.pitch % 12 in A_MINOR.pcs for n in notes)
        assert all(28 <= n.pitch <= 60 for n in notes)
        assert all(n.instrument == "bass" for n in notes)


def test_comp_voices_the_chord():
    for energy in (1, 2, 3):
        notes = comp_bar(A_MINOR, energy)
        assert {n.pitch % 12 for n in notes} == set(A_MINOR.pcs)
        assert all(n.instrument == "keys" for n in notes)
