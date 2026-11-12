"""Pure projection of a plan; only StateManager may commit the projection."""
from dataclasses import replace

from jammate.contracts.band_plan import BandPlan, PlanError
from jammate.interaction import Strategy
from jammate.state import JamState


def project(state: JamState, plan: BandPlan) -> JamState:
    data = plan.to_dict()
    if data["session_id"] != state.session_id or data["based_on_state_version"] != state.state_version:
        raise PlanError("plan is based on a different session/state version")
    target = state.with_updates(**{("tempo_bpm" if k == "tempo_bpm" else "playing"): v for k, v in data["transport"].items()})
    for action in data["actions"]:
        name, scope, strength, params = (action[k] for k in ("action", "scope", "strength", "parameters"))
        if name == "FILL" or scope == "guitar":
            raise PlanError("offline renderer does not implement FILL/guitar")
        if name == "SIMPLIFY" and scope == "lead":
            raise PlanError("baseline cannot simplify a lead motif without losing its notes")
        if name == "PRESERVE":
            continue
        if name in {"ECHO_MOTIF", "VARY_MOTIF"}:
            try:
                target.motif(params["motif_id"])
            except StopIteration as exc:
                raise PlanError("unknown motif reference") from exc
            target = target.with_updates(current_motif_id=params["motif_id"])
            continue
        if name == "INTRODUCE_MOTIF":
            if any(m.motif_id == params["motif_id"] for m in target.motifs):
                raise PlanError("new motif id is already in use")
            continue  # Generated notes and the new motif arrive with a successful receipt.
        if "rhythm_event_id" in params:
            if name != "ADJUST_FEEL" or scope != "band":
                raise PlanError("rhythm adoption must be ADJUST_FEEL/band")
            found = [r.context for r in target.rhythm_sources if r.event_id == params["rhythm_event_id"]]
            if not found:
                raise PlanError("unknown rhythm reference")
            target = target.with_updates(rhythm=found[0])
        if "brightness" in params:
            if name != "ADJUST_FEEL" or scope != "band":
                raise PlanError("brightness adjustment must target band")
            # This baseline deliberately rejects rather than pretending a timbre change.
            raise PlanError("offline renderer has no brightness mapping")
        layers = []
        matched = False
        for layer in target.layers:
            if scope == "band" or layer.name == scope:
                matched = True
                if name == "BUILD":
                    layer = replace(layer, level=min(1., layer.level + .5 * strength))
                elif name == "SIMPLIFY":
                    if layer.name != "lead":
                        layer = replace(layer, density=max(0., layer.density - .5 * strength))
                elif name in {"ADD_LAYER", "REMOVE_LAYER"}:
                    layer = replace(layer, active=name == "ADD_LAYER")
                elif name == "ADJUST_FEEL" and "level_delta" in params:
                    layer = replace(layer, level=max(0., min(1., layer.level + params["level_delta"])))
            layers.append(layer)
        if not matched:
            raise PlanError(f"unsupported action scope: {scope}")
        target = target.with_updates(layers=tuple(layers))
    for preserved in data["preserve"]:
        if preserved == "tempo" and target.tempo_bpm != state.tempo_bpm:
            raise PlanError("plan violates preserve tempo")
        if preserved == "playing" and target.playing != state.playing:
            raise PlanError("plan violates preserve playing")
        if preserved == "key" and (target.key, target.progression_variant) != (state.key, state.progression_variant):
            raise PlanError("plan violates preserve key/harmony")
        if preserved == "groove" and target.rhythm != state.rhythm:
            raise PlanError("plan violates preserve groove")
        if preserved.startswith("motif:"):
            motif_id = preserved[6:]
            changes_motif = any(a["action"] in {"VARY_MOTIF", "INTRODUCE_MOTIF"} for a in data["actions"])
            if motif_id not in {m.motif_id for m in state.motifs} or target.current_motif_id != state.current_motif_id or changes_motif:
                raise PlanError("plan violates preserve motif")
        if preserved.startswith("layer:") and target.layer(preserved[6:]) != state.layer(preserved[6:]):
            raise PlanError("plan violates preserved layer")
    strategy = Strategy(data["strategy"])
    return target.with_updates(interaction=replace(target.interaction, strategy=strategy,
                               initiative=1.0 if strategy is Strategy.INITIATE else 0.0))
