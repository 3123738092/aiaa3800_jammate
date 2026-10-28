from dataclasses import replace
import random

import numpy as np
import pytest

from jammate.director.commands import parse_command
from jammate.director.policy import UserCommand, UserRhythm, decide
from jammate.generate.arranger import arrange_bar, chord_for_bar
from jammate.music import Key
from jammate.pipeline_rhythm import jam_rhythm
from jammate.rhythm import RhythmContext, RhythmMode
from jammate.state import Action, JamState

pytestmark = pytest.mark.unit

A = RhythmContext(100, (0, 1, 1.5, 3), (1, .5, .8, 1))
B = RhythmContext(100, (0, .5, 2, 3.5), (1, .5, .8, 1))


def _starts(notes, instrument):
    return {n.start for n in notes if n.instrument == instrument}


def test_same_bpm_different_rhythms_change_all_three_parts():
    a = arrange_bar(JamState(rhythm=A, energy=2), 0)
    b = arrange_bar(JamState(rhythm=B, energy=2), 0)
    for instrument in ("drums", "bass", "keys"):
        assert _starts(a, instrument) == set(A.onset_beats)
        assert _starts(b, instrument) == set(B.onset_beats)
        assert _starts(a, instrument) != _starts(b, instrument)


@pytest.mark.parametrize("mode", RhythmMode)
def test_modes_preserve_anchors_or_choose_a_sparse_subset(mode):
    notes = arrange_bar(JamState(rhythm=A, rhythm_mode=mode, energy=2), 0)
    starts = _starts(notes, "bass")
    anchors = set(A.onset_beats)
    if mode is RhythmMode.FOLLOW:
        assert starts == anchors
    elif mode is RhythmMode.VARY:
        assert anchors < starts
    else:
        assert starts < anchors and len(starts) <= 2


@pytest.mark.parametrize("mode", RhythmMode)
@pytest.mark.parametrize("key", [Key(0), Key(4), Key(9, "minor")])
def test_parts_share_bar_boundaries_and_remain_in_the_current_chord(mode, key):
    state = JamState(rhythm=A, rhythm_mode=mode, energy=3, key=key)
    for bar in range(4):
        notes = arrange_bar(state, bar)
        assert all(bar * 4 <= n.start < n.end <= (bar + 1) * 4 for n in notes)
        assert all(1 <= n.velocity <= 127 for n in notes)
        chord = chord_for_bar(state, bar)
        assert all(n.pitch % 12 in chord.pcs for n in notes if n.instrument != "drums")


def test_input_accents_control_each_part_without_changing_tempo():
    state = JamState(rhythm=RhythmContext(100, (0, 2), (1, .2)), energy=2)
    notes = arrange_bar(state, 0)
    for instrument in ("drums", "bass", "keys"):
        loud = [n.velocity for n in notes if n.instrument == instrument and n.start == 0]
        quiet = [n.velocity for n in notes if n.instrument == instrument and n.start == 2]
        assert max(loud) > max(quiet)
    assert state.tempo_bpm == 96  # renderer uses the authoritative playback tempo


def test_break_and_stop_override_rhythm():
    assert {n.instrument for n in arrange_bar(JamState(rhythm=A, energy=0), 0)} == {"keys"}
    assert arrange_bar(JamState(rhythm=A, playing=False), 0) == ()


def test_low_confidence_or_empty_updates_leave_existing_groove_and_tempo_intact():
    state = JamState(rhythm=A, tempo_bpm=100)
    for context in (replace(B, confidence=.3), RhythmContext(120, (), ())):
        decision = decide(state, UserRhythm(context), random.Random(0))
        assert decision.action is Action.HOLD and decision.state == state


def test_first_rhythm_sets_tempo_and_subsequent_updates_are_bounded():
    first = decide(JamState(), UserRhythm(A), random.Random(0))
    assert first.state.tempo_bpm == 100
    updated = decide(first.state, UserRhythm(replace(B, tempo_bpm=160)), random.Random(0))
    assert updated.state.rhythm == replace(B, tempo_bpm=160)
    assert updated.state.tempo_bpm == 108


def test_captured_rhythm_does_not_undo_a_newer_tempo_command():
    state = JamState(tempo_bpm=120, rhythm=A)
    updated = decide(state, UserRhythm(B, update_tempo=False), random.Random(0))
    assert updated.state.tempo_bpm == 120 and updated.state.rhythm == B


@pytest.mark.parametrize("text,mode", [("follow rhythm", RhythmMode.FOLLOW),
                                       ("变化节奏", RhythmMode.VARY), ("简化", RhythmMode.SIMPLIFY)])
def test_mode_commands_are_independent_of_bpm_and_energy(text, mode):
    state = JamState(rhythm=A, energy=2)
    updated = decide(state, UserCommand(parse_command(text)), random.Random(0)).state
    assert updated.rhythm_mode is mode
    assert updated.tempo_bpm == state.tempo_bpm and updated.energy == state.energy


def test_offline_output_has_three_parts_trailing_rest_and_explicit_output_origin():
    context = replace(A, beat_origin_s=10)
    result = jam_rhythm(context, bars=2, start_s=.5)
    assert {n.instrument for n in result.notes} == {"drums", "bass", "keys"}
    assert not result.audio[:round(.5 * result.sr)].any()
    assert len(result.audio) == round((.5 + 8 * .6 + 1.5) * result.sr)
    assert np.isfinite(result.audio).all() and np.abs(result.audio).max() <= .99
    assert jam_rhythm(context, bars=2, start_s=.5).notes == result.notes
