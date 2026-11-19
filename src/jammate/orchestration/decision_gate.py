"""Decide whether a plan with unresolved work has an executable part."""

from .transitions import project


def should_execute(state, plan, *, local_arrangement=False):
    data = plan.to_dict()
    if not data["unresolved"]:
        return True
    target = project(state, plan)
    if (target.tempo_bpm, target.playing, target.layers, target.rhythm) != (
        state.tempo_bpm, state.playing, state.layers, state.rhythm
    ):
        return True
    if not target.playing:
        return False
    return any(
        action["action"] in {"INTRODUCE_MOTIF", "ECHO_MOTIF", "VARY_MOTIF"}
        or (local_arrangement and action["action"] == "ADJUST_FEEL"
            and action["scope"] in {"drums", "bass", "keys"}
            and f"layer:{action['scope']}" not in data["preserve"]
            and action["parameters"] == {"level_delta": 0})
        for action in data["actions"]
    )
