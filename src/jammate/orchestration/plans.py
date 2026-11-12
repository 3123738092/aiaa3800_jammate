"""Bind trusted runtime metadata to a planner's musical decision."""
from jammate.contracts import BandPlan


def make_plan(state, events, plan_id, strategy, actions, preserve, transport, bars, reason, unresolved):
    return BandPlan.from_dict({"schema_version": "1.0.0", "session_id": state.session_id,
        "plan_id": plan_id, "based_on_state_version": state.state_version,
        "trigger_event_ids": [e.event_id for e in events], "strategy": strategy,
        "effective_at": {"bar": state.position.next_bar, "beat": 0.}, "valid_for_bars": bars,
        "preserve": list(dict.fromkeys(preserve)), "actions": actions, "transport": transport,
        "plan_confidence": None, "reason": reason, "unresolved": unresolved})
