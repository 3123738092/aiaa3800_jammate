"""Resolve an unspecified instrument request against the current lineup."""
import re

from jammate.instruments import ADDED_INSTRUMENTS
from .explicit_request import request_hold
from .plans import make_plan
from .tempo_intent import speech_clauses


_ADD_INSTRUMENTS = re.compile(
    r"(?:请|能不能|可以)?\s*(?:再)?(?:加入|加上|添加|加|增加)\s*"
    r"(?:一?些|几个|一?点|一[个种件])?\s*(?:别的|其他|新)?\s*乐器(?:进来|吧)?"
    r"|(?:please\s+)?add (?:some |more |another |an? )?instruments?"
)
_NAMES = {"drums": "鼓", "bass": "贝斯", "keys": "键盘", "lead": "主旋律",
          **{name: instrument.label for name, instrument in ADDED_INSTRUMENTS.items()}}


def asks_for_instruments(events):
    clauses = speech_clauses(events)
    return len(clauses) == 1 and _ADD_INSTRUMENTS.fullmatch(clauses[0]) is not None


def unspecified_instrument_plan(events, state, plan_id):
    if not asks_for_instruments(events):
        return None
    if any(layer.name == "guitar" for layer in state.layers):
        available = [instrument.label for name, instrument in ADDED_INSTRUMENTS.items()
                     if all(layer.name != name for layer in state.layers)]
        if available:
            return request_hold(events, state, plan_id,
                                "木吉他已经加入。还能加" + "、".join(available)
                                + f"。可以说「加上{available[0]}」，或让大模型 Agent 按当前风格选择。")
        muted = next((layer.name for layer in state.layers if not layer.active), None)
        if muted:
            label = _NAMES.get(muted, muted)
            option = f"当前{label}已静音，可以让它回来。"
        else:
            option = "想让现有编制更饱满，可以说「键盘更有力」或「鼓更有力」。"
        return request_hold(events, state, plan_id, "可加入的乐器已经都在编制中。" + option)
    if not state.playing:
        return request_hold(events, state, plan_id, "当前演奏已停止。可以先说「继续」，再加入木吉他。")
    preserve = ["tempo", "key", "playing", "groove", *(f"layer:{layer.name}" for layer in state.layers
                                                       if layer.name != "lead")]
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    return make_plan(
        state, events, plan_id, "FOLLOW",
        [{"action": "INTRODUCE_LAYER", "scope": "guitar", "strength": .5, "parameters": {}}],
        preserve, {}, 4, "当前可以新增木吉他，这一轮加入木吉他分解和弦。", [],
    )
