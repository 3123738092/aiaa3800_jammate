"""Bind trusted runtime metadata to a planner's musical decision."""
from jammate.contracts import BandPlan


def make_plan(state, events, plan_id, strategy, actions, preserve, transport, bars, reason, unresolved, **intent):
    return BandPlan.from_dict({"schema_version": "1.0.0", "session_id": state.session_id,
        "plan_id": plan_id, "based_on_state_version": state.state_version,
        "trigger_event_ids": [e.event_id for e in events], "strategy": strategy,
        "effective_at": {"bar": state.position.next_bar, "beat": 0.}, "valid_for_bars": bars,
        "preserve": list(dict.fromkeys(preserve)), "actions": actions, "transport": transport,
        "plan_confidence": None, "reason": reason, "unresolved": unresolved, **intent})


def preserve_part_textures(plan, state):
    """Apply the independent texture settings only to the parts open for editing."""
    data = plan.to_dict()
    preserve = set(data["preserve"])
    protected = {name: getattr(state.texture, name)
                 for name in ("drums", "bass", "keys") if f"layer:{name}" in preserve}
    if "groove" in preserve:
        protected["drums"] = state.texture.drums
    changed = False
    for action in data["actions"]:
        if action["action"] == "SET_TEXTURE":
            changed |= any(action["parameters"].get(part) != value for part, value in protected.items())
            action["parameters"].update(protected)
    return BandPlan.from_dict(data) if changed else plan
