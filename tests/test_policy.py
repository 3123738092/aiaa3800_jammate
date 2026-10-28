import random

import pytest

from jammate.director.commands import Intent
from jammate.director.policy import Decision, Silence, UserCommand, UserPhrase, decide
from jammate.music import Key, Note
from jammate.state import Action, JamState

pytestmark = pytest.mark.unit


def _phrase(pitches, beat=1.0):
    return UserPhrase(tuple(Note(p, i * beat, beat, instrument="user") for i, p in enumerate(pitches)))


def _decide(state, event, seed=0) -> Decision:
    decision = decide(state, event, random.Random(seed))
    assert decision.reason, "every decision must be explainable to the user"
    return decision


def test_phrase_in_key_is_answered_and_remembered():
    state = JamState()
    decision = _decide(state, _phrase([60, 62, 64, 67]))
    assert decision.action is Action.FOLLOW
    assert decision.lead
    assert decision.state.key == state.key
    assert decision.state.last_user_motif[0].start == 0.0
    assert decision.state.last_ai_motif == decision.lead


def test_phrase_far_outside_the_key_moves_the_band():
    e_major_tune = [64, 66, 68, 71, 68, 66, 64, 63, 64]
    decision = _decide(JamState(key=Key(0, "major")), _phrase(e_major_tune))
    new_key = decision.state.key
    assert new_key != Key(0, "major")
    assert all(new_key.contains(p) for p in e_major_tune)
    assert "moved to" in decision.reason


def test_empty_phrase_is_ignored():
    decision = _decide(JamState(), UserPhrase(()))
    assert decision.action is Action.HOLD and decision.lead == ()


@pytest.mark.parametrize("intent,action,check", [
    (Intent.FASTER, Action.ADJUST, lambda s: s.tempo_bpm == 104.0),
    (Intent.SLOWER, Action.ADJUST, lambda s: s.tempo_bpm == 88.0),
    (Intent.MORE_ENERGY, Action.ADJUST, lambda s: s.energy == 2),
    (Intent.LESS_ENERGY, Action.ADJUST, lambda s: s.energy == 0),
    (Intent.BUILD, Action.BUILD, lambda s: s.energy == 2),
    (Intent.BREAK, Action.BREAK, lambda s: s.energy == 0),
    (Intent.KEEP, Action.HOLD, lambda s: s == JamState()),
    (Intent.VARY, Action.VARY, lambda s: s.progression_variant == 1),
    (Intent.STOP, Action.STOP, lambda s: not s.playing),
])
def test_single_commands(intent, action, check):
    decision = _decide(JamState(), UserCommand((intent,)))
    assert decision.action is action
    assert check(decision.state)


def test_new_idea_throws_a_motif():
    decision = _decide(JamState(), UserCommand((Intent.NEW_IDEA,)))
    assert decision.action is Action.INITIATE
    assert decision.lead and decision.state.last_ai_motif == decision.lead


def test_vary_reworks_the_last_ai_idea():
    first = _decide(JamState(), _phrase([60, 62, 64, 67]))
    varied = _decide(first.state, UserCommand((Intent.VARY,)), seed=1)
    assert varied.lead and varied.lead != first.lead


def test_combined_commands_apply_all_changes_and_pick_strongest_action():
    decision = _decide(JamState(), UserCommand((Intent.FASTER, Intent.NEW_IDEA)))
    assert decision.action is Action.INITIATE
    assert decision.state.tempo_bpm == 104.0


def test_any_command_after_stop_resumes_playing():
    stopped = JamState(playing=False)
    decision = _decide(stopped, UserCommand((Intent.MORE_ENERGY,)))
    assert decision.state.playing


def test_silence_triggers_an_invitation_after_two_bars():
    assert _decide(JamState(), Silence(bars=1)).action is Action.HOLD
    decision = _decide(JamState(), Silence(bars=2))
    assert decision.action is Action.INITIATE and decision.lead


def test_silence_when_stopped_stays_quiet():
    decision = _decide(JamState(playing=False), Silence(bars=8))
    assert decision.action is Action.HOLD and decision.lead == ()


def test_unknown_event_type_is_rejected():
    with pytest.raises(TypeError):
        decide(JamState(), "hello", random.Random(0))  # type: ignore[arg-type]


def test_singing_after_stop_resumes_so_the_answer_is_heard():
    decision = _decide(JamState(playing=False), _phrase([60, 62, 64, 67]))
    assert decision.action is Action.FOLLOW and decision.state.playing
    assert "resumed" in decision.reason
