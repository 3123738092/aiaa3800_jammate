"""Compile standalone instructions that name an exact destination key."""
import re

from jammate.music import Key, PITCH_CLASS_NAMES
from .explicit_request import request_hold
from .plans import make_plan
from .tempo_intent import speech_clauses


_TARGET = re.compile(
    r"(?:请\s*|please\s+)?"
    r"(?:(?:把(?:调性|曲子)\s*)?(?:换成|换到|换为|转成|转到|改成|改为|切换到|切换成)"
    r"|(?:调性|曲调)(?:设为|调到|改为|换到)"
    r"|(?:change|switch|transpose|modulate)(?:\s+(?:the\s+)?key)?\s+to)\s*"
    r"([a-g])\s*([#♯b♭]?)\s*(大调|小调|major|minor)(?:\s*吧)?"
)
_UNSPECIFIED = re.compile(
    r"(?:请\s*|please\s+)?(?:换(?:一个|个|一)?调|转(?:个)?调|改(?:个|一下)?调性|"
    r"switch keys?|change (?:the )?key|transpose|modulate)(?:\s*吧)?"
)
_TONICS = {name.lower(): pc for pc, name in enumerate(PITCH_CLASS_NAMES) if len(name) == 1}
_ACCIDENTALS = {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}


def explicit_key_plan(events, state, plan_id):
    clauses = speech_clauses(events)
    if len(clauses) != 1:
        return None
    text = clauses[0]
    if _UNSPECIFIED.fullmatch(text):
        return request_hold(events, state, plan_id, "想换到哪个调？例如「换成 D 大调」或「换成 A 小调」。")
    match = _TARGET.fullmatch(text)
    if match is None:
        return None
    root, accidental, mode_text = match.groups()
    tonic = (_TONICS[root] + _ACCIDENTALS[accidental]) % 12
    mode = "major" if mode_text in {"大调", "major"} else "minor"
    key = Key(tonic, mode)
    label = f"{PITCH_CLASS_NAMES[tonic]} {'大调' if mode == 'major' else '小调'}"
    if state.key == key:
        return request_hold(events, state, plan_id, f"当前已经是 {label}。")
    preserve = ["tempo", "playing"]
    if state.key is not None:
        preserve.extend(("groove", "layer:drums"))
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    return make_plan(
        state, events, plan_id, "FOLLOW",
        [{"action": "SET_KEY", "scope": "band", "strength": 1.0, "parameters": {"tonic": tonic, "mode": mode}}],
        preserve, {}, 4, f"从生效小节起，将伴奏换到 {label}。", [],
    )
