"""Pure projection of a plan; only StateManager may commit the projection."""
from dataclasses import replace

from jammate.contracts.band_plan import BandPlan, PlanError
from jammate.generate.harmony import harmony_cycle, same_cycle
from jammate.instruments import MAX_JAM_LAYERS, added_program
from jammate.interaction import Layer, Strategy
from jammate.music import ChordStep, Key
from jammate.rhythm import RhythmContext
from jammate.state import JamState
from jammate.texture import ArrangementTexture


def project(state: JamState, plan: BandPlan) -> JamState:
    data = plan.to_dict()
    if data["session_id"] != state.session_id or data["based_on_state_version"] != state.state_version:
        raise PlanError("plan is based on a different session/state version")
    target = state.with_updates(**data["transport"])
    if target.beats_per_bar != state.beats_per_bar:
        rhythm = state.rhythm
        beats = target.beats_per_bar
        if beats == 2:
            rhythm = RhythmContext(target.tempo_bpm, tuple(i / 3 for i in range(6)),
                                   (1., .35, .4, .72, .35, .4), beats_per_bar=2)
        elif rhythm:
            if state.beats_per_bar == 2:
                rhythm = None
            else:
                rhythm = replace(rhythm, beats_per_bar=beats,
                                 onset_beats=tuple(b * beats / rhythm.beats_per_bar for b in rhythm.onset_beats))
        target = target.with_updates(rhythm=rhythm, position=replace(state.position, beat=0., beats_per_bar=beats))
    if "style" in data:
        target = target.with_updates(style=data["style"])
    for action in data["actions"]:
        name, scope, strength, params = (action[k] for k in ("action", "scope", "strength", "parameters"))
        if name == "FILL":
            raise PlanError("offline renderer does not implement FILL")
        if name == "SET_KEY":
            target = target.with_updates(key=Key(int(params["tonic"]), params["mode"]))
            continue
        if name == "SET_PROGRESSION":
            if target.key is None:
                raise PlanError("a chord progression needs a configured key")
            steps = tuple(ChordStep(int(chord["offset"]), chord["quality"]) for chord in params["chords"])
            if len(set(steps)) < 2:
                raise PlanError("a progression needs at least two distinct chords")
            if same_cycle(harmony_cycle(target), tuple(step.in_key(target.key) for step in steps)):
                raise PlanError("choose a different chord progression, not a rotation or repeat of the current one")
            target = target.with_updates(chord_cycle=steps, progression_start_bar=data["effective_at"]["bar"])
            continue
        if name == "SET_TEXTURE":
            target = target.with_updates(texture=ArrangementTexture(**params))
            continue
        if name == "INTRODUCE_LAYER":
            if any(layer.name == scope for layer in target.layers):
                raise PlanError(f"layer already exists: {scope}")
            if any(added_program(layer.name) == added_program(scope) for layer in target.layers):
                raise PlanError(f"the timbre for {scope} is already in the arrangement")
            if len(target.layers) >= MAX_JAM_LAYERS:
                raise PlanError("all MIDI channels are occupied")
            target = target.with_updates(layers=(*target.layers, Layer(scope)))
            continue
        if added_program(scope) is not None and not any(layer.name == scope for layer in target.layers):
            raise PlanError(f"{scope} must be introduced before it can be adjusted")
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
        if preserved == "progression" and (target.chord_cycle, target.progression_variant) != (
            state.chord_cycle, state.progression_variant
        ):
            raise PlanError("plan violates preserve progression")
        if preserved == "groove" and target.rhythm != state.rhythm:
            raise PlanError("plan violates preserve groove")
        if preserved.startswith("motif:"):
            motif_id = preserved[6:]
            changes_motif = any(a["action"] in {"VARY_MOTIF", "INTRODUCE_MOTIF"} for a in data["actions"])
            if motif_id not in {m.motif_id for m in state.motifs} or target.current_motif_id != state.current_motif_id or changes_motif:
                raise PlanError("plan violates preserve motif")
        if preserved.startswith("layer:"):
            name = preserved[6:]
            before = next((layer for layer in state.layers if layer.name == name), None)
            after = next((layer for layer in target.layers if layer.name == name), None)
            if before is None or after != before:
                raise PlanError("plan violates preserved layer")
    strategy = Strategy(data["strategy"])
    return target.with_updates(interaction=replace(target.interaction, strategy=strategy,
                               allow_initiative=data.get("allow_initiative", target.interaction.allow_initiative),
                               initiative=1.0 if strategy is Strategy.INITIATE else 0.0))
