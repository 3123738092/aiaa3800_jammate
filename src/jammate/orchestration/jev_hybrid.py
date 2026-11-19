"""Opt-in Jev slot selection followed by the ordinary LLM decision when needed."""
import time
from uuid import uuid4

from jammate.contracts import PlanError
from .explicit_request import compile_jev_request, explicit_request, request_hold
from .llm import ProviderFailure, validate_plan


class JevHybridAgent:
    """The selector proposes slots. Only the host can turn them into a plan."""

    def __init__(self, selector, fallback):
        self.selector = selector
        self.fallback = fallback
        self.last_trace = {}

    def decide(self, events, state, *, plan_id=None, history=()):
        events = tuple(events)
        plan_id = plan_id or f"plan_{uuid4().hex}"
        start = time.monotonic()
        self.last_trace = {"plan_id": plan_id, "jev": None}
        try:
            request = explicit_request(events, state=state)
            if request is None:
                return self._fallback(events, state, plan_id, history, "outside_supported_requests")
            if reason := request.refusal(state):
                self.last_trace.update(source="request_gate", reason=reason)
                return request_hold(events, state, plan_id, reason)
            try:
                slots, trace = self.selector(events, state)
                self.last_trace["jev"] = trace
                plan = None if not isinstance(slots, dict) else compile_jev_request(
                    events, state, plan_id, tempo=slots.get("tempo"),
                    layer_changes=slots.get("layer_changes"), bars=slots.get("bars"),
                )
                if plan is not None:
                    validate_plan(plan, events, state)
                    self.fallback.execution_validator(state, plan)
            except (PlanError, ProviderFailure) as exc:
                reason = exc.kind if isinstance(exc, ProviderFailure) else "plan_rejected"
            else:
                if plan is not None:
                    self.last_trace["source"] = "jev"
                    return plan
                reason = "candidate_does_not_cover_request" if slots else "no_jev_candidate"
            return self._fallback(events, state, plan_id, history, reason)
        finally:
            self.last_trace["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)

    def _fallback(self, events, state, plan_id, history, reason):
        self.last_trace.update(source="llm_fallback", reason=reason)
        try:
            return self.fallback.decide(events, state, plan_id=plan_id, history=history)
        finally:
            self.last_trace["fallback"] = self.fallback.last_trace
