import pytest

from jammate.generate.arranger import arrange_bar, chord_for_bar, place_lead
from jammate.generate.harmony import progression
from jammate.music import Key, Note
from jammate.state import JamState

pytestmark = pytest.mark.unit


def test_chord_follows_the_loop():
    state = JamState(key=Key(0, "major"))
    loop = progression(state.key, state.progression_variant)
    assert [chord_for_bar(state, i) for i in range(5)] == [*loop, loop[0]]


def test_arrange_bar_places_everything_in_absolute_time():
    state = JamState(energy=2)
    lead = (Note(72, 0.0, 1.0),)
    notes = arrange_bar(state, bar_index=3, lead=lead)
    assert all(12.0 <= n.start < 16.0 for n in notes)
    assert {"drums", "bass", "keys", "lead"} <= {n.instrument for n in notes}


def test_break_bar_has_no_drums_or_bass():
    notes = arrange_bar(JamState(energy=0), bar_index=0)
    assert {n.instrument for n in notes} == {"keys"}


def test_stopped_state_is_silent():
    assert arrange_bar(JamState(playing=False), bar_index=0, lead=(Note(72, 0, 1),)) == ()


def test_place_lead_splits_a_motif_across_bars():
    motif = (Note(60, 0.0, 1.0), Note(62, 3.5, 1.0), Note(64, 5.0, 2.0))
    placed = place_lead(motif, start_bar=2)
    assert sorted(placed) == [2, 3]
    assert [n.start for n in placed[2]] == [0.0, 3.5]
    assert [n.start for n in placed[3]] == [1.0]
