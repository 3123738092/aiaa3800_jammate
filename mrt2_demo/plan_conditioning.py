"""Translate BandPlan intent into MusicCoCa style text without compiling a score."""

import re

from jammate.music import PITCH_CLASS_NAMES
from jammate.instruments import gm_program
from jammate.render.mrt2_style import STYLE_LIMIT, english_style
from pretty_midi import program_to_instrument_name


PARTS = {"band": "ensemble", "drums": "drum kit", "keys": "keyboards",
         "lead": "lead melody", "motif": "melody", "guitar": "acoustic guitar"}

_PART_WORDS = {
    "drums": r"\b(?:drums?|percussion)\b",
    "bass": r"\bbass(?:line)?\b",
    "keys": r"\b(?:keys|keyboards?|piano)\b",
    "guitar": r"\bguitars?\b",
    "lead": r"\b(?:lead|melody|melodic|harmony)\b",
}

def part_name(scope):
    program = gm_program(scope)
    return program_to_instrument_name(program).lower() if program is not None else PARTS.get(scope, scope)


def score_stream_style(style, state, *, lead_voice=None):
    """Describe the current audible lineup before any background style cues."""
    active = [part for part in state.layers if part.active and part.level > 0 and part.density > 0]
    names = [part_name(lead_voice if part.name == "lead" and lead_voice else part.name) for part in active]
    joined = ", ".join(names)
    families = {part.name for part in active}
    for family, pattern in _PART_WORDS.items():
        if re.search(pattern, joined, re.I):
            families.add(family)
    pieces = [("solo " if len(active) == 1 else "") + (("prominent " if part.level >= .65 else
               "soft " if part.level < .4 else "") + ("sparse " if part.density < .8 else "") + name)
              for part, name in zip(active, names)]
    for cue in re.split(r"[,;]", style or ""):
        cue = cue.strip()
        if not cue:
            continue
        mentioned = {part for part, pattern in _PART_WORDS.items() if re.search(pattern, cue, re.I)}
        negative = re.match(r"^(?:no|without)\b", cue, re.I)
        if negative and families - {"drums"} and re.search(r"\bharmony\b", cue, re.I):
            continue
        if (negative and mentioned & families) or (not negative and mentioned - families):
            continue
        if len(active) > 1:
            cue = re.sub(r"\b(?:solo|only|unaccompanied)\b", "", cue, flags=re.I).strip()
        if cue and len(", ".join((*pieces, cue))) <= STYLE_LIMIT:
            pieces.append(cue)
    prompt = ", ".join(pieces)
    if len(prompt) > STYLE_LIMIT:
        prompt = prompt[:STYLE_LIMIT].rsplit(", ", 1)[0]
    return english_style(prompt)


def describe_action(action, state):
    name, scope, params = action["action"], action["scope"], action["parameters"]
    part = part_name(scope)
    if name in {"ECHO_MOTIF", "VARY_MOTIF"}:
        notes = state.motif(params["motif_id"]).notes[:12]
        pitches = " ".join(PITCH_CLASS_NAMES[note.pitch % 12] for note in notes)
        verb = "Echo" if name == "ECHO_MOTIF" else "Vary"
        return f"{verb} the melody {pitches}"
    if name == "INTRODUCE_MOTIF":
        return "Introduce a new lead melody"
    if name == "SIMPLIFY":
        return f"Sparser {part}"
    if name == "BUILD":
        return f"More prominent {part}"
    if name == "REMOVE_LAYER":
        return f"Without {part}"
    if name == "ADJUST_FEEL":
        delta = params.get("level_delta", 0)
        if delta:
            return f"{'Louder' if delta > 0 else 'Softer'} {part}"
        if "rhythm_event_id" not in params:
            return f"Change the feel of the {part}"
    return None


def band_plan_style(base_style, state, plan, *, tempo=False, key=False):
    if not state.playing:
        return None

    style = state.style or base_style
    actions = plan.actions if plan is not None else ()
    details = [description for action in actions if (description := describe_action(action, state))]
    if not style and not details and not state.rhythm and not state.chord_cycle:
        return None
    if tempo:
        details.append(f"{state.tempo_bpm:g} BPM")
    if key and state.key is not None:
        details.append(state.key.name)
    if state.key is not None and state.chord_cycle:
        details.append("Chords " + " ".join(step.in_key(state.key).name for step in state.chord_cycle))

    instruments = []
    solo = re.search(r"\bsolo\b", style or "", re.I)
    excludes_keys = re.search(r"\b(?:no|without) keyboards?\b", style or "", re.I)
    for layer in state.layers:
        if (layer.active and layer.level > 0 and layer.density > 0 and layer.name != "lead"
                and not solo and not (layer.name == "keys" and excludes_keys)):
            part = part_name(layer.name)
            dynamics = "soft " if layer.level < .4 else "strong " if layer.level > .7 else ""
            density = "sparse " if layer.density < .8 else ""
            if dynamics or density or part not in (style or "").casefold():
                instruments.append(dynamics + density + part)
    if instruments:
        details.append(", ".join(instruments))
    if state.rhythm and state.rhythm.onset_beats:
        details.append("Rhythm accents on beats " + ", ".join(f"{beat + 1:g}" for beat in state.rhythm.onset_beats))
    details.extend(f"{value} {part}" for part, value in vars(state.texture).items() if value != "default")
    pieces = [style] if style else []
    for detail in dict.fromkeys(details):
        if len(", ".join((*pieces, detail))) <= STYLE_LIMIT:
            pieces.append(detail)
    return english_style(", ".join(pieces)) if pieces else None
