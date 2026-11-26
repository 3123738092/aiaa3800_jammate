"""Recognize explicit user ownership controls before a generative decision."""
import re

from .plans import make_plan
from .tempo_intent import speech_clauses

_KEEP_ENSEMBLE = re.compile(
    r"(?:保持(?:这个|当前|原来的)?(?:节奏|伴奏)|keep (?:this|the) (?:groove|rhythm))"
    r"(?:\s*(?:and )?(?:不要加(?:入)?其他乐器|别加(?:其他)?乐器|no other instruments))?")
_NO_VARIATION = {"关闭主动变化", "不要主动变化", "不要加其他乐器", "保持原样", "保持这样",
                 "就这个感觉继续", "keep it as is", "no more variations", "disable variations"}
_ALLOW_VARIATION = {"你可以自由发挥", "可以自由发挥", "自由发挥", "恢复自主创作",
                    "feel free to improvise", "you can improvise", "enable variations"}


def initiative_plan(events, state, plan_id):
    clauses = speech_clauses(events)
    if len(clauses) == 1 and clauses[0] in _ALLOW_VARIATION:
        allowed = True
    elif clauses and (all(clause in _NO_VARIATION for clause in clauses)
                      or (len(clauses) <= 2 and _KEEP_ENSEMBLE.fullmatch(clauses[0]) and
                          all(clause in _NO_VARIATION or clause == "no other instruments" for clause in clauses[1:]))):
        allowed = False
    else:
        return None
    return make_plan(state, events, plan_id, "FOLLOW", [], ["tempo", "key", "progression", "groove", "playing"],
                     {}, 1, "已允许主动提出新想法。" if allowed else "保持当前演奏，暂停主动变化。", [],
                     allow_initiative=allowed)
