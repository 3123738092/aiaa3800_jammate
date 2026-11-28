"""Coordinate events and plans on a sample clock, with a virtual playback driver.

A session has one owner. Only that owner ingests, binds, schedules and advances
its clock. Agent decisions and bound-plan preparation can run on workers.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace

from jammate.contracts import BandPlan, InputEvent, PlanError
from jammate.generate.harmony import harmony_cycle
from jammate.interaction import TransportPosition
from jammate.music import Note
from jammate.render.clock import BarClock, BarTiming
from jammate.state import JamState
from .decision_gate import should_execute
from .llm import validate_plan
from .manager import StateManager
from .offline import PlannedPerformance, apply_layer_settings, prepare_bar, prepare_plan
from .transitions import project


@dataclass(frozen=True)
class TimingPolicy:
    enqueue_guard_ms: int = 200
    preparation_budget_ms: int = 350
    lookahead_bars: int = 3

    def __post_init__(self):
        for value in (self.enqueue_guard_ms, self.preparation_budget_ms):
            if type(value) is not int or value < 0:
                raise ValueError("timing margins must be nonnegative integer milliseconds")
        if type(self.lookahead_bars) is not int or self.lookahead_bars < 1:
            raise ValueError("lookahead_bars must be a positive integer")


@dataclass(frozen=True)
class ScheduleSlot:
    bar: int
    start_sample: int
    enqueue_deadline_sample: int
    decision_deadline_sample: int


@dataclass(frozen=True)
class DecisionRequest:
    events: tuple[InputEvent, ...]
    state: JamState
    history: tuple[dict, ...]
    last_candidate_bar: int
    decision_deadline_sample: int

    def decide(self, agent) -> BandPlan:
        return agent.decide(self.events, self.state, history=self.history)


@dataclass(frozen=True)
class BoundDecision:
    request: DecisionRequest
    slot: ScheduleSlot
    plan: BandPlan
    state: JamState

    def keeps_playing_material(self) -> bool:
        data = self.plan.to_dict()
        return (all(action["action"] == "PRESERVE" for action in data["actions"])
                and data.get("style", self.state.style) == self.state.style
                and all(getattr(self.state, field) == value for field, value in data["transport"].items()))

    def updates_conditioning(self) -> bool:
        return not self.keeps_playing_material() or any(
            event.source == "humming" and event.to_dict()["provenance"].get("audio_ref")
            for event in self.request.events
        )

    def prepare(self, arrange=None) -> PreparedChange:
        """Worker-side note preparation using a frozen snapshot."""
        validate_plan(self.plan, self.request.events, self.state)
        if not should_execute(self.state, self.plan, local_arrangement=arrange is not None):
            raise PlanError("plan needs clarification or has no executable change")
        performance = (prepare_plan(self.state, self.plan) if arrange is None
                       else arrange(self.state, self.plan, self.request.events))
        return PreparedChange(self, performance)


@dataclass(frozen=True)
class PreparedChange:
    decision: BoundDecision
    performance: PlannedPerformance
    repeat_layers: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RepeatingPart:
    name: str
    performance: PlannedPerformance
    state: JamState


@dataclass(frozen=True)
class PlaybackStarted:
    session_id: str
    plan_id: str
    bar: int
    start_sample: int


@dataclass(frozen=True)
class PlaybackBar:
    timing: BarTiming
    notes: tuple[Note, ...]
    started: PlaybackStarted | None = None
    plan: BandPlan | None = None
    state: JamState | None = None


def _notes_in_bar(performance: PlannedPerformance, bar: int) -> tuple[Note, ...]:
    bar_length = performance.beats_per_bar
    start = bar * bar_length
    return tuple(note for note in performance.notes if start <= note.start < start + bar_length)


def prepare_conditioning(state, plan, _events):
    """Validate plan intent and timing for a generator that owns its output."""
    target = project(state, plan)
    data = plan.to_dict()
    return PlannedPerformance((), (), None, target.tempo_bpm,
                              data["effective_at"]["bar"], data["valid_for_bars"], beats_per_bar=target.beats_per_bar)


class RealtimeSession:
    """Virtual sample-driven host for the live scheduling contract."""

    def __init__(self, manager: StateManager, *, sample_rate: int = 22050,
                 timing: TimingPolicy = TimingPolicy(), opening_plan: BandPlan | None = None,
                 opening_answer: tuple[Note, ...] = (), conditioning_only: bool = False):
        if type(sample_rate) is not int or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        initial = manager.state
        if initial.position != TransportPosition(beats_per_bar=initial.beats_per_bar):
            raise ValueError("a new realtime session must start at bar zero")
        if initial.playing and initial.auto_harmony and initial.key is None:
            raise ValueError("a playing realtime session needs a key")
        self.manager = manager
        self.sample_rate = sample_rate
        self.timing = timing
        self._clock = BarClock(sample_rate, beats_per_bar=initial.beats_per_bar)
        self._sample = 0
        self._playing_bar: BarTiming | None = None
        self._request: DecisionRequest | None = None
        self._queued: PreparedChange | None = None
        self._active: PlannedPerformance | None = None
        self._active_plan: BandPlan | None = None
        self._repeating_parts: tuple[_RepeatingPart, ...] = ()
        self.conditioning_only = conditioning_only
        if opening_plan is not None:
            request = DecisionRequest(manager.current_batch(), initial, (), 0, 0)
            decision = BoundDecision(request, ScheduleSlot(0, 0, 0, 0), opening_plan, initial)
            performance = (prepare_conditioning(initial, opening_plan, request.events) if conditioning_only
                           else prepare_plan(initial, opening_plan, opening_answer=opening_answer))
            if not manager.stage(opening_plan):
                raise PlanError("opening plan has already been staged")
            self._queued = PreparedChange(decision, performance)

    @property
    def sample(self) -> int:
        return self._sample

    def schedule_window(self) -> tuple[ScheduleSlot, ...]:
        """Upcoming writable boundaries in the same sample domain as advance_to."""
        clock = copy(self._clock)
        bpm = self.manager.state.tempo_bpm
        guard = round(self.sample_rate * self.timing.enqueue_guard_ms / 1000)
        preparation = round(self.sample_rate * self.timing.preparation_budget_ms / 1000)
        slots = []
        while len(slots) < self.timing.lookahead_bars:
            timing = clock.reserve(bpm)
            enqueue_by = timing.start_sample - guard
            if enqueue_by >= self.sample and timing.start_sample > self.sample:
                slots.append(ScheduleSlot(timing.bar_index, timing.start_sample,
                                          enqueue_by, enqueue_by - preparation))
        return tuple(slots)

    def observe(self, events: tuple[InputEvent, ...]) -> DecisionRequest | None:
        accepted = self.manager.ingest(events)
        if not accepted:
            return None
        self._queued = None  # Accepted input invalidated the old pending plan.
        state, history = self.manager.decision_snapshot()
        last_slot = self.schedule_window()[-1]
        request = DecisionRequest(accepted, state, tuple(history), last_slot.bar,
                                  last_slot.decision_deadline_sample)
        self._request = request
        return request

    def _require_current(self, request: DecisionRequest) -> JamState:
        state = self.manager.state
        if request is not self._request or request.state.state_version != state.state_version:
            raise PlanError("stale decision request")
        return state

    def bind(self, request: DecisionRequest, plan: BandPlan, *, preparation_budget_ms=None,
             after_bar=-1, through_bar=None) -> BoundDecision:
        """Choose the earliest slot with sufficient time left for preparation."""
        state = self._require_current(request)
        data = plan.to_dict()
        if (data["session_id"] != state.session_id
                or data["based_on_state_version"] != request.state.state_version
                or tuple(data["trigger_event_ids"]) != tuple(event.event_id for event in request.events)):
            raise PlanError("plan does not match the decision request")
        budget = self.timing.preparation_budget_ms if preparation_budget_ms is None else preparation_budget_ms
        samples = round(self.sample_rate * budget / 1000)
        last_bar = request.last_candidate_bar if through_bar is None else min(through_bar, request.last_candidate_bar)
        slot = next((s for s in self.schedule_window()
                     if after_bar < s.bar <= last_bar
                     and self.sample <= s.enqueue_deadline_sample - samples), None)
        if slot is None:
            raise PlanError("decision deadline missed")
        data["effective_at"] = {"bar": slot.bar, "beat": 0.}
        # The shared note compiler expects the preparation target as next_bar.
        # This disposable view does not change the actual playback position.
        source = state.with_updates(position=replace(state.position, next_bar=slot.bar))
        return BoundDecision(request, slot, BandPlan.from_dict(data), source)

    def schedule(self, change: PreparedChange, *, reuse_local_score=False, repeat_layers=()) -> bool:
        """Queue one prepared plan, rechecking request and deadline on the owner."""
        decision = change.decision
        self._require_current(decision.request)
        if (reuse_local_score and self._active is not None and not self.conditioning_only
                and change.performance.beats_per_bar == self._active.beats_per_bar):
            change = self._reuse_local_score(change)
        if repeat_layers:
            change = replace(change, repeat_layers=repeat_layers)
        if self._queued is not None:
            if self._queued is change or ((reuse_local_score or repeat_layers) and self._queued == change):
                return False
            raise PlanError("a plan is already scheduled for this decision")
        if self.sample > decision.slot.enqueue_deadline_sample or decision.slot not in self.schedule_window():
            raise PlanError("enqueue deadline missed")
        self._check_preserved_playback(change)
        if not self.manager.stage(decision.plan):
            return False
        self._queued = change
        return True

    def _reuse_local_score(self, change):
        """Preserve queued musical material for deterministic browser edits."""
        plan = change.decision.plan
        data = plan.to_dict()
        actions = [action for action in data["actions"] if action["action"] != "PRESERVE"]
        mix_only = bool(actions) and all(
            action["action"] == "SIMPLIFY" or
            (action["action"] == "ADJUST_FEEL" and action["parameters"].get("level_delta"))
            for action in actions
        )
        key_only = len(actions) == 1 and actions[0]["action"] == "SET_KEY"
        motif_only = len(actions) == 1 and actions[0]["action"] in {"ECHO_MOTIF", "VARY_MOTIF", "INTRODUCE_MOTIF"}
        tempo_only = not actions and set(data["transport"]) == {"tempo_bpm"}
        if not (mix_only or key_only or motif_only or tempo_only) or data["transport"].get("playing") is False:
            return change

        source = change.decision.state
        target = project(source, plan)
        protected = {item[6:] for item in data["preserve"] if item.startswith("layer:")}
        if tempo_only:
            protected = {layer.name for layer in source.layers}
        editable = {layer.name for layer in target.layers if mix_only and layer.name not in protected
                    and layer != source.layer(layer.name)}
        active, incoming = self._active, change.performance
        first = max(active.start_bar, incoming.start_bar)
        end = min(active.start_bar + active.bars, incoming.start_bar + incoming.bars)
        if first >= end or not (protected or editable):
            return change

        bar_length = source.beats_per_bar
        notes = [note for note in incoming.notes if not first * bar_length <= note.start < end * bar_length]
        for bar in range(first, end):
            old, new = _notes_in_bar(active, bar), _notes_in_bar(incoming, bar)
            reused = {note.instrument for note in old if note.instrument in editable}
            notes.extend(note for note in new if note.instrument not in protected | reused)
            notes.extend(note for note in old if note.instrument in protected)
            for name in reused:
                before, after = source.layer(name), target.layer(name)
                part = tuple(note for note in old if note.instrument == name)
                density = min(1., after.density / before.density) if before.density else 1.
                filter_state = target.with_updates(layers=tuple(
                    replace(layer, density=density, level=.5) if layer.name == name else layer
                    for layer in target.layers
                ))
                gain = after.level / before.level if before.level else 1.
                if after.active and gain:
                    notes.extend(replace(note, velocity=max(1, min(127, round(note.velocity * gain))))
                                 for note in apply_layer_settings(part, filter_state, bar=bar))
        performance = replace(incoming, notes=tuple(sorted(notes, key=lambda n: (n.start, n.instrument, n.pitch))))
        return replace(change, performance=performance)

    def _check_preserved_playback(self, change: PreparedChange):
        active = self._active
        if active is None or change.decision.keeps_playing_material():
            return
        data = change.decision.plan.to_dict()
        if data["transport"].get("playing") is False:
            return
        incoming = change.performance
        end = min(active.start_bar + active.bars, incoming.start_bar + incoming.bars)
        for bar in range(incoming.start_bar, end):
            old, new = _notes_in_bar(active, bar), _notes_in_bar(incoming, bar)
            for preserve in data["preserve"]:
                if preserve.startswith("layer:"):
                    layer = preserve[6:]
                    if tuple(n for n in old if n.instrument == layer) != tuple(n for n in new if n.instrument == layer):
                        raise PlanError(f"new plan would change queued {preserve}")
                elif preserve == "groove":
                    old_groove = tuple((n.pitch, n.start, n.duration) for n in old if n.instrument == "drums")
                    new_groove = tuple((n.pitch, n.start, n.duration) for n in new if n.instrument == "drums")
                    if old_groove != new_groove:
                        raise PlanError("new plan would change queued groove")

    def preview_next_bar(self) -> PlaybackBar:
        """Read the next bar for buffering without confirming its playback."""
        return self.preview_bars(1)[0]

    def preview_bars(self, count: int) -> tuple[PlaybackBar, ...]:
        """Project a short buffer without advancing the transport or applying a receipt."""
        clock = copy(self._clock)
        state, performance, plan = self.manager.state, self._active, self._active_plan
        parts = self._repeating_parts
        queued = self._queued
        bars = []
        for _ in range(count):
            if (queued is not None and queued.decision.slot.bar == clock.bar_index
                    and queued.decision.slot.start_sample == clock.next_sample
                    and self.manager.plan_status(queued.decision.plan.plan_id) == "pending"):
                state = project(state, queued.decision.plan)
                if not queued.decision.keeps_playing_material():
                    performance = queued.performance
                parts = self._repeat_after_change(parts, queued, state)
                if plan is None or queued.decision.updates_conditioning():
                    plan = queued.decision.plan
            clock.beats_per_bar = state.beats_per_bar
            timing = clock.reserve(state.tempo_bpm)
            bars.append(self._make_bar(timing, state, performance, plan, parts=parts))
        return tuple(bars)

    @staticmethod
    def _repeat_after_change(parts, change, state):
        if not state.playing or not (parts or change.repeat_layers):
            return ()
        chords = harmony_cycle(state)
        data = change.decision.plan.to_dict()
        rewritten = {action["scope"] for action in data["actions"]
                     if action["action"] not in {"PRESERVE", "SIMPLIFY"} and not (
                         action["action"] == "ADJUST_FEEL" and action["parameters"].keys() == {"level_delta"}
                     )}
        retained = []
        for part in parts:
            source = part.state
            if (part.name in change.repeat_layers or not state.layer(part.name).active
                    or source.beats_per_bar != state.beats_per_bar or source.key != state.key
                    or source.style != state.style or source.progression_start_bar != state.progression_start_bar
                    or harmony_cycle(source) != chords):
                continue
            if f"layer:{part.name}" in data["preserve"] or not rewritten & {part.name, "band"}:
                retained.append(part)
        # Loop only whole harmonic cycles so subsequent bars keep their chord alignment.
        if change.performance.bars % (len(chords) or 1) == 0:
            retained.extend(_RepeatingPart(name, change.performance, state) for name in change.repeat_layers)
        return tuple(retained)

    def _make_bar(self, timing, state, performance, plan, receipt=None, *, parts=()):
        if plan is not None:
            data = plan.to_dict()
            if timing.bar_index >= data["effective_at"]["bar"] + data["valid_for_bars"]:
                plan = None
        notes = () if self.conditioning_only else self._bar_notes(state, performance, timing.bar_index)
        for part in parts:
            bar = timing.bar_index
            pattern = part.performance
            original = pattern.start_bar + (bar - pattern.start_bar) % pattern.bars
            before, after = part.state.layer(part.name), state.layer(part.name)
            shift = (bar - original) * state.beats_per_bar
            line = tuple(n.shifted(shift) for n in _notes_in_bar(pattern, original) if n.instrument == part.name)
            if before != after:
                # The saved notes already include the original mix settings.
                density = min(1., after.density / before.density)
                gain = after.level / before.level
                relative_layer = replace(after, density=density, level=.5)
                relative_state = state.with_updates(layers=tuple(
                    relative_layer if layer.name == part.name else layer for layer in state.layers
                ))
                line = tuple(replace(n, velocity=max(1, min(127, round(n.velocity * gain))))
                             for n in apply_layer_settings(line, relative_state, bar=bar) if gain)
            backing = tuple(n for n in notes if n.instrument != part.name)
            notes = tuple(sorted(backing + line, key=lambda n: (n.start, n.instrument, n.pitch)))
        return PlaybackBar(timing, notes, receipt, plan=plan, state=state)

    @staticmethod
    def _bar_notes(state, performance, bar):
        if performance is not None and bar < performance.start_bar + performance.bars:
            return _notes_in_bar(performance, bar)
        return prepare_bar(state, bar)

    def _start_bar(self) -> PlaybackBar:
        bar, start = self._clock.bar_index, self._clock.next_sample
        self.manager.update_transport(bar=bar, beat=0., next_bar=bar,
                                      completed_previous_bar=self._playing_bar is not None)
        receipt = None
        queued = self._queued
        if queued is not None and queued.decision.slot.bar == bar:
            self._queued = None
            if (queued.decision.slot.start_sample == start
                    and self.manager.plan_status(queued.decision.plan.plan_id) == "pending"):
                plan, performance = queued.decision.plan, queued.performance
                if self.manager.playback_started(plan, bar=bar, start_sample=start,
                                                 lead_notes=performance.lead_notes,
                                                 generated_motif=performance.generated_motif,
                                                 conditioning_only=self.conditioning_only):
                    receipt = PlaybackStarted(self.manager.state.session_id, plan.plan_id, bar, start)
                    if not queued.decision.keeps_playing_material():
                        self._active = performance
                    self._repeating_parts = self._repeat_after_change(self._repeating_parts, queued, self.manager.state)
                    if self._active_plan is None or queued.decision.updates_conditioning():
                        self._active_plan = plan
        state = self.manager.state
        self._clock.beats_per_bar = state.beats_per_bar
        timing = self._clock.reserve(state.tempo_bpm)
        self._playing_bar = timing
        if receipt is None:
            self.manager.update_transport(bar=bar, beat=0., next_bar=bar + 1)
        if self._active is not None and bar >= self._active.start_bar + self._active.bars:
            self._active = None
        return self._make_bar(timing, state, self._active, self._active_plan, receipt, parts=self._repeating_parts)

    def advance_to(self, sample: int) -> tuple[PlaybackBar, ...]:
        """Stand in for the playback driver, reporting bars as they start."""
        if type(sample) is not int or sample < self.sample:
            raise ValueError("playback samples must be nondecreasing integers")
        started = []
        while self._clock.next_sample <= sample:
            self._sample = self._clock.next_sample
            started.append(self._start_bar())
        self._sample = sample
        playing = self._playing_bar
        if playing is not None:
            beat = self.manager.state.beats_per_bar * (sample - playing.start_sample) / (playing.end_sample - playing.start_sample)
            self.manager.update_transport(bar=playing.bar_index, beat=beat, next_bar=playing.bar_index + 1)
        return tuple(started)
