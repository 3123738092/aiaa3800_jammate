"""Resolve an unspecified instrument request against the current lineup."""
import re

from jammate.instruments import ADDED_INSTRUMENTS, MAX_JAM_LAYERS, added_program
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
_ADD_GUITAR_RIFF = re.compile(
    r"(?:请\s*)?(?:再\s*)?(?:加上?|加入|来|给我来)\s*(?:一?[段条个句])?\s*"
    r"(?:清楚的|清晰的|清音的?|干净的)?\s*电吉他\s*(?:riff|乐句)")
_HIGHLIGHT_GUITAR = re.compile(r"(?:把)?(?:电吉他|吉他)(?:再)?(?:突出|大声|响)(?:一点|一些)?")
_MUTE_GUITAR = re.compile(r"(?:去掉|移除|停掉|关掉|静音)(?:电吉他|吉他)")
_BACKING = r"(?:鼓组?|鼓点|bass|贝斯)(?:\s*(?:和|与|及|and)\s*(?:鼓组?|鼓点|bass|贝斯))*"
_KEEP_BACKING = re.compile(rf"(?:(?:保留|保持)\s*{_BACKING}|{_BACKING}\s*(?:也|都)?(?:保持|保持不变|不要变|不变|照旧))")


def guitar_command_plan(events, state, plan_id, soundfont_programs):
    """Ground an explicit guitar turn without giving the generic lead another part."""
    clauses = [text for text in speech_clauses(events) if not _KEEP_BACKING.fullmatch(text)]
    if len(clauses) != 1 or not state.playing:
        return None
    text = clauses[0]
    target = events[0].to_dict()["provenance"].get("target")
    if target not in (None, "band", "guitar") and not 24 <= (added_program(target) or 0) <= 31:
        return None

    needs_score = _ADD_GUITAR_RIFF.fullmatch(text) is not None
    if needs_score:
        if state.key is None or 27 not in (soundfont_programs or {}):
            return None
        scope = "gm_27"
        if target not in (None, "band", "guitar", scope):
            return None
        existing = next((layer for layer in state.layers if added_program(layer.name) == 27), None)
        if existing and existing.active:
            return None
        if not existing and len(state.layers) >= MAX_JAM_LAYERS:
            return request_hold(events, state, plan_id, "当前编制已满，电吉他还未加入。"), False
        scope = existing.name if existing else scope
        action, params = ("ADD_LAYER" if existing else "INTRODUCE_LAYER"), {}
        reason = "保留现有声部，为清音电吉他写一段 riff。"
    elif _HIGHLIGHT_GUITAR.fullmatch(text) or _MUTE_GUITAR.fullmatch(text):
        first_program = 26 if "电吉他" in text else 24
        matches = [layer.name for layer in state.layers if layer.active
                   and first_program <= (added_program(layer.name) or 0) <= 31
                   and (target in (None, "band", "guitar") or target == layer.name)]
        if not matches:
            return request_hold(events, state, plan_id, "当前没有在演奏的吉他声部。"), False
        if len(matches) != 1:
            return request_hold(events, state, plan_id, "当前有多把吉他，请指明要调整哪一把。"), False
        scope = matches[0]
        mute = _MUTE_GUITAR.fullmatch(text) is not None
        action, params = ("REMOVE_LAYER", {}) if mute else ("ADJUST_FEEL", {"level_delta": .2})
        reason = "移除指定吉他声部。" if mute else "提高指定吉他声部的音量。"
    else:
        return None

    preserve = ["tempo", "key", "progression", "playing", "groove",
                *(f"layer:{layer.name}" for layer in state.layers if layer.name != scope)]
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    return make_plan(state, events, plan_id, "FOLLOW",
                     [{"action": action, "scope": scope, "strength": .5, "parameters": params}],
                     preserve, {}, 4, reason, []), needs_score


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
