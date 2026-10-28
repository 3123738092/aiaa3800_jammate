"""Rule-based band director: (state, event) -> decision.

Every decision carries a short `reason` so the interface can tell the user
why the band did what it did (human agency: understand / correct / override).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Union

from jammate.analysis.key import estimate_key
from jammate.director.commands import Intent
from jammate.generate.response import answer_motif, initiate_motif, normalize
from jammate.music import Note
from jammate.rhythm import RhythmContext, RhythmMode
from jammate.state import Action, JamState


@dataclass(frozen=True)
class UserPhrase:
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class UserCommand:
    intents: tuple[Intent, ...]
    source: str = "text"  # text | voice | gesture


@dataclass(frozen=True)
class Silence:
    bars: int


@dataclass(frozen=True)
class UserRhythm:
    context: RhythmContext
    mode: RhythmMode | None = None
    update_tempo: bool = True


Event = Union[UserPhrase, UserCommand, Silence, UserRhythm]


@dataclass(frozen=True)
class Decision:
    action: Action
    state: JamState
    lead: tuple[Note, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class PolicyConfig:
    tempo_step_bpm: float = 8.0
    initiate_after_bars: int = 2
    out_of_key_ratio: float = 0.34  # share of sung time outside the current key
    min_key_margin: float = 0.02
    min_rhythm_confidence: float = 0.65
    max_rhythm_tempo_step: float = 8.0


# Strongest first: when several intents arrive together, the reported action
# is the most noticeable change; all state changes are still applied.
_PRECEDENCE = (Action.STOP, Action.INITIATE, Action.VARY, Action.BREAK, Action.BUILD,
               Action.ADJUST, Action.HOLD)


def decide(state: JamState, event: Event, rng: random.Random,
           config: PolicyConfig = PolicyConfig()) -> Decision:
    if isinstance(event, UserPhrase):
        return _on_phrase(state, event, rng, config)
    if isinstance(event, UserCommand):
        return _on_command(state, event, rng, config)
    if isinstance(event, Silence):
        return _on_silence(state, event, rng, config)
    if isinstance(event, UserRhythm):
        return _on_rhythm(state, event, config)
    raise TypeError(f"unsupported event {type(event).__name__}")


def _on_rhythm(state: JamState, event: UserRhythm, config: PolicyConfig) -> Decision:
    rhythm = event.context
    if not rhythm.onset_beats or rhythm.confidence < config.min_rhythm_confidence:
        return Decision(Action.HOLD, state, reason="uncertain or empty rhythm; keeping the groove")
    tempo = rhythm.tempo_bpm if event.update_tempo else state.tempo_bpm
    if state.rhythm is not None and event.update_tempo:
        delta = max(-config.max_rhythm_tempo_step,
                    min(config.max_rhythm_tempo_step, tempo - state.tempo_bpm))
        tempo = state.tempo_bpm + delta
    mode = RhythmMode(event.mode) if event.mode is not None else state.rhythm_mode
    new_state = state.with_tempo(tempo).with_updates(rhythm=rhythm, rhythm_mode=mode, playing=True)
    return Decision(Action.FOLLOW, new_state,
                    reason=f"{mode.value} rhythm with {len(rhythm.onset_beats)} anchors at {tempo:.1f} BPM")


def _out_of_key_ratio(notes: tuple[Note, ...], state: JamState) -> float:
    total = sum(n.duration for n in notes)
    outside = sum(n.duration for n in notes if not state.key.contains(n.pitch))
    return outside / total if total else 0.0


def _on_phrase(state: JamState, event: UserPhrase, rng: random.Random,
               config: PolicyConfig) -> Decision:
    notes = normalize(event.notes)
    if not notes:
        return Decision(Action.HOLD, state, reason="heard nothing to answer")
    key, key_note = state.key, ""
    if _out_of_key_ratio(notes, state) > config.out_of_key_ratio:
        estimate = estimate_key(notes)
        if estimate and estimate.key != key and estimate.margin >= config.min_key_margin:
            key, key_note = estimate.key, f"; moved to {estimate.key.name} to match you"
    answer = answer_motif(notes, key, rng)
    new_state = state.with_updates(key=key, last_user_motif=notes, last_ai_motif=answer.notes,
                                   playing=True)
    resumed = "" if state.playing else "; resumed"  # singing means the user wants to play
    reason = f"answered your phrase with a {answer.transform}{key_note}{resumed}"
    return Decision(Action.FOLLOW, new_state, answer.notes, reason)


def _apply_intent(state: JamState, intent: Intent, config: PolicyConfig) -> tuple[JamState, Action, str]:
    step = config.tempo_step_bpm
    rhythm_modes = {Intent.RHYTHM_FOLLOW: RhythmMode.FOLLOW,
                    Intent.RHYTHM_VARY: RhythmMode.VARY,
                    Intent.RHYTHM_SIMPLIFY: RhythmMode.SIMPLIFY}
    if intent in rhythm_modes:
        mode = rhythm_modes[intent]
        return state.with_updates(rhythm_mode=mode), Action.ADJUST, f"rhythm mode {mode.value}"
    if intent is Intent.FASTER:
        new = state.with_tempo(state.tempo_bpm + step)
        return new, Action.ADJUST, f"tempo {new.tempo_bpm:.0f} BPM"
    if intent is Intent.SLOWER:
        new = state.with_tempo(state.tempo_bpm - step)
        return new, Action.ADJUST, f"tempo {new.tempo_bpm:.0f} BPM"
    if intent in (Intent.MORE_ENERGY, Intent.LESS_ENERGY, Intent.BUILD):
        delta = -1 if intent is Intent.LESS_ENERGY else 1
        new = state.with_energy(state.energy + delta)
        action = Action.BUILD if intent is Intent.BUILD else Action.ADJUST
        return new, action, f"energy {new.energy}"
    if intent is Intent.BREAK:
        return state.with_energy(0), Action.BREAK, "breakdown: pads only"
    if intent is Intent.VARY:
        new = state.with_updates(progression_variant=state.progression_variant + 1)
        return new, Action.VARY, "new chord loop"
    if intent is Intent.NEW_IDEA:
        return state, Action.INITIATE, "offering a new motif"
    if intent is Intent.STOP:
        return state.with_updates(playing=False), Action.STOP, "stopping"
    return state, Action.HOLD, "keeping the groove"


def _on_command(state: JamState, event: UserCommand, rng: random.Random,
                config: PolicyConfig) -> Decision:
    actions, reasons, new_state = set(), [], state
    for intent in event.intents:
        new_state, action, note = _apply_intent(new_state, intent, config)
        actions.add(action)
        reasons.append(note)
    action = next((a for a in _PRECEDENCE if a in actions), Action.HOLD)
    if action is not Action.STOP and not new_state.playing:
        new_state = new_state.with_updates(playing=True)
        reasons.append("resumed")
    lead: tuple[Note, ...] = ()
    if action is Action.INITIATE:
        lead = initiate_motif(new_state.key, rng)
    elif action is Action.VARY:
        source = new_state.last_ai_motif or new_state.last_user_motif
        lead = (answer_motif(source, new_state.key, rng).notes if source
                else initiate_motif(new_state.key, rng))
    if lead:
        new_state = new_state.with_updates(last_ai_motif=lead)
    return Decision(action, new_state, lead, "; ".join(reasons) or "keeping the groove")


def _on_silence(state: JamState, event: Silence, rng: random.Random,
                config: PolicyConfig) -> Decision:
    if not state.playing or event.bars < config.initiate_after_bars:
        return Decision(Action.HOLD, state, reason="waiting for you")
    lead = initiate_motif(state.key, rng)
    reason = f"you were quiet for {event.bars} bars, so here is an idea to answer"
    return Decision(Action.INITIATE, state.with_updates(last_ai_motif=lead), lead, reason)
