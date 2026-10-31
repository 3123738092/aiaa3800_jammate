"""Single session writer with transactional intake, pending plans and receipts.

The in-memory journal is process-local. JSONL tracing is durable evidence,
not an automatic checkpoint/recovery protocol.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import replace

from jammate.contracts import InputEvent
from jammate.contracts.adapters import to_legacy_phrase
from jammate.contracts.band_plan import BandPlan, PlanError
from jammate.interaction import Interaction, Motif, RhythmSource, Strategy, TransportPosition
from jammate.music import Note
from jammate.rhythm import RhythmContext
from jammate.session_log import SessionLog
from jammate.state import JamState
from .transitions import project


class StateManager:
    def __init__(self, initial: JamState, log: SessionLog | None = None):
        self._state = JamState.from_dict(initial.to_dict())
        self._lock = threading.RLock()
        self._log = log or SessionLog(None)
        self._log_write_failures = 0
        self._last_log_error = None
        self._events: dict[str, InputEvent] = {}
        self._plans: dict[str, BandPlan] = {}
        self._status: dict[str, str] = {}
        self._batch_ids: tuple[str, ...] = ()
        self._watermark: float | None = None
        self._turns: list[dict] = []
        self._emit("state_initialized", state=self._state.to_dict())

    def _emit(self, event_type, **fields):
        # This journal is evidence, not a transactional database. A full disk
        # must not interrupt a mutation halfway through or disguise a commit.
        try:
            self._log.write(event_type, **fields)
        except OSError as exc:
            self._log_write_failures += 1
            self._last_log_error = {"event_type": event_type, "error": type(exc).__name__, "errno": exc.errno}

    def diagnostics(self):
        with self._lock:
            return {"log_write_failures": self._log_write_failures,
                    "last_log_error": dict(self._last_log_error) if self._last_log_error else None,
                    "event_count": len(self._events), "plan_count": len(self._plans), "turn_count": len(self._turns)}

    def current_batch(self) -> tuple[InputEvent, ...]:
        """Accepted, not yet successfully consumed input; a retry uses a NEW plan ID."""
        with self._lock:
            return tuple(self._events[event_id] for event_id in self._batch_ids)

    @property
    def state(self):
        with self._lock:
            return self._state

    def plan_status(self, plan_id):
        with self._lock:
            return self._status.get(plan_id)

    @staticmethod
    def _context_state(state):
        data = state.to_dict()
        return {**{k: data[k] for k in ("state_version", "transport", "harmony", "feel", "arrangement", "interaction")},
                "current_motif_id": state.current_motif_id}

    def decision_snapshot(self, *, history_limit=6):
        """One coherent snapshot plus recent observed/executed turn history.

        History is evidence, never a second mutable music state. Stale input is
        excluded, and pending/failed/cancelled plans retain their real status.
        """
        if type(history_limit) is not int or not 0 <= history_limit <= 20:
            raise ValueError("history_limit must be within 0..20")
        with self._lock:
            history = []
            failed_batches = {tuple(p.to_dict()["trigger_event_ids"]) for key, p in self._plans.items()
                              if self._status[key] == "failed"}
            prior = [turn for turn in self._turns if turn["event_ids"] != self._batch_ids or turn["event_ids"] in failed_batches]
            for turn in prior[-history_limit:] if history_limit else ():
                plans = [{"plan": p.to_dict(), "status": self._status[p.plan_id]} for p in self._plans.values()
                         if tuple(p.to_dict()["trigger_event_ids"]) == turn["event_ids"]]
                history.append({"events": [self._events[e].to_dict() for e in turn["event_ids"]],
                                "state_before": deepcopy(turn["state_before"]),
                                "state_after": deepcopy(turn["state_after"]) if "state_after" in turn else None,
                                "plans": plans})
            return self._state, history

    def ingest(self, events) -> tuple[InputEvent, ...]:
        """One explicit related batch. Does not guess grouping from arrival time."""
        events = tuple(events)
        with self._lock:
            proposed = {}
            for event in events:
                if not isinstance(event, InputEvent) or event.session_id != self._state.session_id:
                    raise ValueError("input belongs to another session or is not an InputEvent")
                prior = proposed.get(event.event_id) or self._events.get(event.event_id)
                if prior is not None and prior != event:
                    raise ValueError("event_id reused with different content")
                proposed[event.event_id] = event
            accepted, stale = [], []
            for event in proposed.values():
                if event.event_id in self._events:
                    continue
                # Old recognition results are retained as evidence, not used to undo newer decisions.
                if event.ended_at is not None and self._watermark is not None and event.ended_at < self._watermark:
                    stale.append(event)
                else:
                    accepted.append(event)
            state = self._state
            for event in accepted:
                if event.source != "system":
                    state = state.with_updates(interaction=replace(state.interaction, awaiting_user=False))
                if event.event_type == "speech":
                    state = state.with_updates(interaction=replace(state.interaction, last_feedback=event.payload["text"]))
                elif event.event_type == "musical_phrase":
                    phrase = to_legacy_phrase(event, tempo_bpm=state.tempo_bpm)
                    if phrase.notes:
                        motif = Motif(f"motif_{event.event_id}", "user", (event.event_id,), phrase.notes)
                        state = state.with_updates(motifs=state.motifs + (motif,), last_user_motif=phrase.notes)
                elif event.event_type == "rhythm_seed":
                    seed = RhythmSource(event.event_id, RhythmContext(**event.payload["rhythm"]))
                    state = state.with_updates(rhythm_sources=state.rhythm_sources + (seed,))
                elif event.payload["command"] == "user_activity":
                    active = event.payload["active"]
                    state = state.with_updates(interaction=replace(state.interaction, user_active=active,
                                              current_lead="user" if active else state.interaction.current_lead))
            # Commit intake only after the whole batch has passed conversion/validation.
            for event in (*accepted, *stale):
                self._events[event.event_id] = event
                self._emit("input_event", event=event.to_dict(), disposition="stale" if event in stale else "accepted")
            if accepted:
                # Retain the small decision-relevant state, not N copies of the
                # ever-growing music catalog. Full event evidence remains intact.
                self._turns.append({"event_ids": tuple(e.event_id for e in accepted), "state_before": self._context_state(self._state)})
                self._state = state.with_updates(state_version=state.state_version + 1)
                self._batch_ids = tuple(event.event_id for event in accepted)
                times = [e.ended_at for e in accepted if e.ended_at is not None]
                if times:
                    self._watermark = max(times + ([self._watermark] if self._watermark is not None else []))
                self._cancel_pending("new input")
                self._emit("state_observed", trigger_event_ids=list(self._batch_ids), state=self._state.to_dict())
            return tuple(accepted)

    def _cancel_pending(self, reason):
        for plan_id, status in self._status.items():
            if status == "pending":
                self._status[plan_id] = "cancelled"
                self._emit("plan_cancelled", plan_id=plan_id, reason=reason)

    def stage(self, plan: BandPlan) -> bool:
        """Validate a candidate without claiming its musical effects happened."""
        with self._lock:
            if plan.plan_id in self._plans:
                if self._plans[plan.plan_id] != plan:
                    raise PlanError("plan_id reused with different content")
                return False
            data = plan.to_dict()
            if tuple(data["trigger_event_ids"]) != self._batch_ids:
                raise PlanError("plan must reference the latest accepted batch in order")
            if data["effective_at"]["beat"] != 0:
                raise PlanError("baseline renderer only executes bar-boundary plans")
            if data["effective_at"]["bar"] < self._state.position.next_bar:
                raise PlanError("plan targets an already rendered bar")
            project(self._state, plan)
            self._cancel_pending("replacement plan")
            self._plans[plan.plan_id] = plan
            self._status[plan.plan_id] = "pending"
            self._emit("band_plan", plan=plan.to_dict())
            return True

    def check_pending(self, plan: BandPlan) -> JamState:
        with self._lock:
            if self._plans.get(plan.plan_id) != plan or self._status.get(plan.plan_id) != "pending":
                raise PlanError("plan is not pending")
            project(self._state, plan)
            if plan.to_dict()["effective_at"]["bar"] < self._state.position.next_bar:
                raise PlanError("plan missed its execution boundary")
            return self._state

    def update_transport(self, *, bar: int, beat: float, next_bar: int):
        """Runtime-owned position updates do not increment semantic state_version."""
        position = TransportPosition(bar, beat, next_bar)
        with self._lock:
            old = self._state.position
            if (bar, beat) < (old.bar, old.beat) or next_bar < old.next_bar:
                raise ValueError("transport cannot move backwards")
            self._state = self._state.with_updates(position=position)
            for plan_id, plan in self._plans.items():
                if self._status[plan_id] == "pending" and plan.to_dict()["effective_at"]["bar"] < next_bar:
                    self._status[plan_id] = "expired"
                    self._emit("plan_expired", plan_id=plan_id)

    def acknowledge(self, plan: BandPlan, *, succeeded: bool, effective_bar: int,
                    completed_bar: int, lead_notes: tuple[Note, ...] = (),
                    generated_motif: Motif | None = None, reason: str = "") -> bool:
        """Apply only a still-current successful receipt; duplicate receipt is a no-op."""
        with self._lock:
            if self._plans.get(plan.plan_id) != plan:
                raise PlanError("receipt does not match the submitted plan")
            status = self._status.get(plan.plan_id)
            if status in {"applied", "failed"}:
                return False
            state = self.check_pending(plan)
            data = plan.to_dict()
            expected_bar = data["effective_at"]["bar"]
            if effective_bar != expected_bar or completed_bar != expected_bar + data["valid_for_bars"]:
                raise PlanError("receipt execution range does not match plan")
            if type(succeeded) is not bool:
                raise PlanError("receipt success must be boolean")
            if not succeeded:
                self._status[plan.plan_id] = "failed"
                # Keep the input actionable. Deduplication still prevents replay;
                # an explicit retry creates a new plan against the same version.
                self._emit("execution_receipt", plan_id=plan.plan_id, succeeded=False, reason=reason)
                return True
            target = project(state, plan)
            motif_actions = [a for a in data["actions"] if a["action"] in {"INTRODUCE_MOTIF", "ECHO_MOTIF", "VARY_MOTIF"}]
            if motif_actions:
                action = motif_actions[0]
                if not lead_notes:
                    raise PlanError("motif execution receipt needs actual lead notes")
                if action["action"] == "INTRODUCE_MOTIF":
                    if generated_motif is None or generated_motif.motif_id != action["parameters"]["motif_id"] or generated_motif.owner != "ai" or generated_motif.notes != tuple(lead_notes) or generated_motif.source_event_ids != tuple(data["trigger_event_ids"]):
                        raise PlanError("missing or inconsistent generated motif receipt")
                    target = target.with_updates(motifs=target.motifs + (generated_motif,), current_motif_id=generated_motif.motif_id)
                elif generated_motif is not None:
                    raise PlanError("unexpected generated motif")
                elif action["action"] == "ECHO_MOTIF":
                    expected = tuple(replace(n, instrument="lead") for n in state.motif(action["parameters"]["motif_id"]).notes)
                    if tuple(lead_notes) != expected:
                        raise PlanError("ECHO_MOTIF receipt changed the user's motif")
                target = target.with_updates(last_ai_motif=tuple(lead_notes), interaction=replace(target.interaction,
                         strategy=Strategy.LISTEN, initiative=0., current_lead="user",
                         awaiting_user=action["action"] == "INTRODUCE_MOTIF"))
            elif lead_notes or generated_motif is not None:
                raise PlanError("receipt supplied unsolicited musical material")
            section = "build" if any(a["action"] == "BUILD" for a in data["actions"]) else "space" if any(a["action"] == "SIMPLIFY" for a in data["actions"]) else target.section
            committed = target.with_updates(state_version=state.state_version + 1, section=section,
                section_bars=(state.section_bars if section == state.section else 0) + data["valid_for_bars"],
                position=TransportPosition(completed_bar, 0., completed_bar))
            self._state = JamState.from_dict(committed.to_dict())
            self._turns[-1]["state_after"] = self._context_state(self._state)
            self._status[plan.plan_id] = "applied"
            self._batch_ids = ()
            self._emit("execution_receipt", plan_id=plan.plan_id, succeeded=True, effective_bar=effective_bar, completed_bar=completed_bar)
            self._emit("state_applied", plan_id=plan.plan_id, state=self._state.to_dict())
            return True

    def acknowledge_and_snapshot(self, plan: BandPlan, **receipt):
        """Return the immutable state at this receipt, before another writer runs."""
        with self._lock:
            accepted = self.acknowledge(plan, **receipt)
            return accepted, self._state
