"""Coordinate events and plans on a sample clock, with a virtual playback driver.

A session has one owner. Only that owner ingests, binds, schedules and advances
its clock. Agent decisions and bound-plan preparation can run on workers.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace

from jammate.contracts import BandPlan, InputEvent, PlanError
from jammate.interaction import TransportPosition
from jammate.music import BEATS_PER_BAR, Note
from jammate.render.clock import BarClock, BarTiming
from jammate.state import JamState
from .decision_gate import should_execute
from .llm import validate_plan
from .manager import StateManager
from .offline import PlannedPerformance, prepare_bar, prepare_plan
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
                and all(getattr(self.state, field) == value for field, value in data["transport"].items()))

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


def _notes_in_bar(performance: PlannedPerformance, bar: int) -> tuple[Note, ...]:
    start = bar * BEATS_PER_BAR
    return tuple(note for note in performance.notes if start <= note.start < start + BEATS_PER_BAR)


class RealtimeSession:
    """Virtual sample-driven host for the live scheduling contract."""

    def __init__(self, manager: StateManager, *, sample_rate: int = 22050,
                 timing: TimingPolicy = TimingPolicy(), opening_plan: BandPlan | None = None,
                 opening_answer: tuple[Note, ...] = ()):
        if type(sample_rate) is not int or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        initial = manager.state
        if initial.position != TransportPosition():
            raise ValueError("a new realtime session must start at bar zero")
        if initial.playing and initial.auto_harmony and initial.key is None:
            raise ValueError("a playing realtime session needs a key")
        self.manager = manager
        self.sample_rate = sample_rate
        self.timing = timing
        self._clock = BarClock(sample_rate)
        self._sample = 0
        self._playing_bar: BarTiming | None = None
        self._request: DecisionRequest | None = None
        self._queued: PreparedChange | None = None
        self._active: PlannedPerformance | None = None
        if opening_plan is not None:
            request = DecisionRequest(manager.current_batch(), initial, (), 0, 0)
            decision = BoundDecision(request, ScheduleSlot(0, 0, 0, 0), opening_plan, initial)
            performance = prepare_plan(initial, opening_plan, opening_answer=opening_answer)
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

    def schedule(self, change: PreparedChange) -> bool:
        """Queue one prepared plan, rechecking request and deadline on the owner."""
        decision = change.decision
        self._require_current(decision.request)
        if self._queued is not None:
            if self._queued is change:
                return False
            raise PlanError("a plan is already scheduled for this decision")
        if self.sample > decision.slot.enqueue_deadline_sample or decision.slot not in self.schedule_window():
            raise PlanError("enqueue deadline missed")
        self._check_preserved_playback(change)
        if not self.manager.stage(decision.plan):
            return False
        self._queued = change
        return True

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
        clock = copy(self._clock)
        bar = clock.bar_index
        state, performance = self.manager.state, self._active
        queued = self._queued
        if (queued is not None and queued.decision.slot.bar == bar
                and queued.decision.slot.start_sample == clock.next_sample
                and self.manager.plan_status(queued.decision.plan.plan_id) == "pending"):
            state = project(state, queued.decision.plan)
            if not queued.decision.keeps_playing_material():
                performance = queued.performance
        timing = clock.reserve(state.tempo_bpm)
        return PlaybackBar(timing, self._bar_notes(state, performance, bar))

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
                                                 generated_motif=performance.generated_motif):
                    receipt = PlaybackStarted(self.manager.state.session_id, plan.plan_id, bar, start)
                    if not queued.decision.keeps_playing_material():
                        self._active = performance
        state = self.manager.state
        timing = self._clock.reserve(state.tempo_bpm)
        self._playing_bar = timing
        if receipt is None:
            self.manager.update_transport(bar=bar, beat=0., next_bar=bar + 1)
        if self._active is not None and bar >= self._active.start_bar + self._active.bars:
            self._active = None
        notes = self._bar_notes(state, self._active, bar)
        return PlaybackBar(timing, notes, receipt)

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
            beat = BEATS_PER_BAR * (sample - playing.start_sample) / (playing.end_sample - playing.start_sample)
            self.manager.update_transport(bar=playing.bar_index, beat=beat, next_bar=playing.bar_index + 1)
        return tuple(started)
