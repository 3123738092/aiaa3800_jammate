"""Compile and check fully grounded combinations of basic text commands."""
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re

from jammate.contracts import PlanError
from jammate.instruments import INSTRUMENT_ALIASES
from .plans import make_plan
from .tempo_intent import TempoIntent, speech_clauses, tempo_clause
from .transitions import project


_LAYERS = {
    "鼓": "drums", "鼓组": "drums", "drums": "drums",
    "贝斯": "bass", "低音": "bass", "bass": "bass",
    "键盘": "keys", "钢琴": "keys", "keys": "keys", "keyboard": "keys",
    "主旋律": "lead", "主奏": "lead", "lead": "lead",
    **INSTRUMENT_ALIASES,
}
_NAMES = "|".join(sorted(_LAYERS, key=len, reverse=True))
_TARGETS = rf"(?:{_NAMES})(?:\s*(?:和|与|及|以及|、|and\s+)(?:\s*)(?:{_NAMES}))*"
_MUTED = r"(?:静音|关掉|停掉|停下来|停止演奏|停奏|先停|休息)"
_ACTIVE = r"(?:恢复演奏|继续演奏|取消静音|恢复|解除静音)"
_LAYER_MATCHERS = (
    (False, re.compile(rf"(?:把|让)?\s*({_TARGETS})\s*{_MUTED}")),
    (False, re.compile(rf"(?:静音|关掉|停掉|mute|disable)\s*(?:the\s+)?({_TARGETS})")),
    (True, re.compile(rf"(?:把|让)?\s*({_TARGETS})\s*{_ACTIVE}")),
    (True, re.compile(rf"(?:恢复|启用|开启|unmute|enable|resume)\s*(?:the\s+)?({_TARGETS})")),
)
_KEEP_TEMPO = {"保持速度", "速度保持", "速度不变", "保持原速", "速度别动", "速度照旧",
               "保持原来的速度", "速度保持不变", "keep tempo", "keep the tempo", "keep the tempo unchanged"}
_KEEP_GROOVE = {"保留原有鼓点", "鼓点照旧", "保持鼓点", "节奏保持", "保持原来的鼓点", "保留鼓点",
                "鼓点保持不变", "鼓点别动", "保留原来的鼓点", "保持原有鼓点", "keep the drum pattern"}
_KEEP_OTHERS = {"其他不变", "其他照旧", "其他都不动", "其他保持", "其他全部保持", "其他声部照旧",
                 "保持其他不变", "其余照旧", "keep everything else unchanged", "keep other layers unchanged"}
_MIX_NAMES = rf"(?:其他)?(?:{_NAMES})"
_MIX_TARGETS = rf"{_MIX_NAMES}(?:\s*(?:以及|和|与|及|、)\s*{_MIX_NAMES})*"
_MIX_SPLIT = re.compile(r"\s*(?:以及|和|与|及|、)\s*")
_MIX_DOWN = re.compile(
    rf"(?:把|让)?(?P<layers>{_MIX_TARGETS})(?:的)?(?P<controls>声音以及密度|音量和密度|声音和密度|声音|音量|密度)?"
    r"(?:都)?(?P<verb>压低|降低|小一点|少一点)(?:一点|一些)?")
_MIX_UP = re.compile(rf"(?:突出|调高)(?P<layer>{_NAMES})(?:的?(?:音量|声音))?")
_MIX_COMMENT = re.compile(rf"我(?:还是)?听不到(?P<layer>{_NAMES})")


def explicit_mix_plan(events, state, plan_id):
    if not all(event.event_type == "speech" for event in events):
        return None
    actions, explained, missing = {}, set(), set()
    available = {layer.name for layer in state.layers}
    for clause in speech_clauses(events):
        if clause in _KEEP_OTHERS:
            continue
        if match := _MIX_DOWN.fullmatch(clause):
            layers = [_LAYERS[label.removeprefix("其他")] for label in _MIX_SPLIT.split(match["layers"])]
            controls = match["controls"] or ("密度" if match["verb"] == "少一点" else "音量")
            for layer in layers:
                if layer not in available or not state.layer(layer).active:
                    missing.add(layer)
                    continue
                if controls != "密度":
                    if actions.get((layer, "ADJUST_FEEL"), {}).get("level_delta", 0) > 0:
                        return None
                    actions[layer, "ADJUST_FEEL"] = {"level_delta": -.15}
                if "密度" in controls and layer != "lead":
                    actions[layer, "SIMPLIFY"] = {}
        elif match := _MIX_UP.fullmatch(clause):
            layer = _LAYERS[match["layer"]]
            if layer not in available or not state.layer(layer).active:
                missing.add(layer)
                continue
            if (layer, "ADJUST_FEEL") in actions:
                return None
            actions[layer, "ADJUST_FEEL"] = {"level_delta": .2}
            explained.add(layer)
        elif not ((match := _MIX_COMMENT.fullmatch(clause)) and _LAYERS[match["layer"]] in explained | missing):
            return None
    if not actions:
        return None
    if any(params.get("level_delta", 0) > 0 and (layer, "SIMPLIFY") in actions
           for (layer, _), params in actions.items()):
        return None
    changed = {layer for layer, _ in actions}
    preserve = ["tempo", "key", "progression", "playing",
                *(f"layer:{layer.name}" for layer in state.layers if layer.name not in changed)]
    if ("drums", "SIMPLIFY") not in actions:
        preserve.append("groove")
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    data = [{"action": name, "scope": layer, "strength": .5, "parameters": params}
            for (layer, name), params in actions.items()]
    return make_plan(state, events, plan_id, "FOLLOW", data, preserve, {}, 4,
                     "调整指定声部的音量或密度。",
                     [f"当前会话没有可调整的声部：{layer}" for layer in sorted(missing)])
_COUNTS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, **{"十" + name: i for i, name in enumerate("一二三四五六七八九", 11)},
           "二十": 20}
_COUNT = r"(\d{1,3}|" + "|".join(sorted(_COUNTS, key=len, reverse=True)) + ")"
_BARS = re.compile(rf"(?:只)?(?P<verb>生成|渲染|播放|来|接|给我|只要|render|play|generate)"
                   rf"\s*{_COUNT}\s*(?:小节|bars?)")


@dataclass(frozen=True)
class ExplicitRequest:
    tempo: TempoIntent | None
    keep_tempo: bool
    layer_changes: tuple[tuple[str, bool], ...]
    bars: int | None
    keep_groove: bool
    conflict: str | None = None

    def refusal(self, state):
        if self.conflict:
            return self.conflict
        if self.tempo and (reason := self.tempo.refusal(state)):
            return reason
        if self.bars is not None and not 1 <= self.bars <= 16:
            return "小节数需要在 1–16 之间。"
        available = {layer.name for layer in state.layers}
        if any(layer not in available for layer, _ in self.layer_changes):
            return "请求的声部还没有加入当前作品。"
        return None

    def guide(self, state):
        target = self.tempo.target(state) if self.tempo else state.tempo_bpm
        verbs = [f"{layer}: {'ADD_LAYER' if active else 'REMOVE_LAYER'}" for layer, active in self.layer_changes
                 if state.layer(layer).active != active]
        return (
            f"Grounded requirements for this request: resulting BPM {target}; "
            f"layer changes {', '.join(verbs) or 'none'}; "
            f"requested bars {self.bars if self.bars is not None else 'unspecified'}. "
            "Keep playback, key, current motif and any other layers. "
            "If drums change activation, omit groove from preserve. "
            "If BPM changes, omit tempo from preserve. Omit changed layers from preserve."
        )


def explicit_request(events, *, state=None):
    """Only return a request when every submitted clause has a defined meaning."""
    tempos, layer_changes, bars = set(), {}, set()
    keep_tempo = keep_groove = False
    conflicts = []
    for raw in speech_clauses(events):
        text = re.sub(r"^(?:同时|并且|并|and\s+|also\s+)\s*", "", raw)
        if text in _KEEP_OTHERS:
            continue
        if text in _KEEP_TEMPO:
            keep_tempo = True
        elif text in _KEEP_GROOVE:
            keep_groove = True
        elif intent := tempo_clause(text):
            tempos.add(intent)
        elif match := _BARS.fullmatch(text):
            # "Play" may restart a stopped session. Let the full Agent resolve
            # that transport intent rather than treating it as length alone.
            if match["verb"] in {"播放", "play"} and state is not None and not state.playing:
                return None
            token = match[2]
            bars.add(int(token) if token.isdecimal() else _COUNTS[token])
        else:
            matches = [(active, match) for active, pattern in _LAYER_MATCHERS if (match := pattern.fullmatch(text))]
            if len(matches) != 1:
                return None
            active, match = matches[0]
            for name in re.split(r"\s*(?:和|与|及|以及|、|\band\b)\s*", match[1]):
                layer = _LAYERS[name]
                if layer in layer_changes and layer_changes[layer] != active:
                    conflicts.append("同一个声部同时被要求开启和关闭。")
                layer_changes[layer] = active
    if not (tempos or layer_changes or bars):
        return None
    if len(tempos) > 1 or tempos and keep_tempo:
        conflicts.append("速度要求互相冲突，请确认是否保持原速。")
    if len(bars) > 1:
        conflicts.append("小节数要求互相冲突，请确认片段长度。")
    if "drums" in layer_changes and keep_groove:
        conflicts.append("鼓声部开关与保留原有鼓点的要求冲突。")
    return ExplicitRequest(next(iter(tempos)) if len(tempos) == 1 else None, keep_tempo,
                           tuple(sorted(layer_changes.items())), next(iter(bars)) if len(bars) == 1 else None,
                           keep_groove, conflicts[0] if conflicts else None)


def request_hold(events, state, plan_id, reason):
    return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                     "保持当前作品，等待可执行的指令。", [reason])


def validate_request_plan(request, state, plan):
    data = plan.to_dict()
    changes = dict(request.layer_changes)
    expected_bpm = request.tempo.target(state) if request.tempo else Decimal(str(state.tempo_bpm))
    if data["unresolved"] or (request.bars is not None and data["valid_for_bars"] != request.bars):
        raise PlanError("explicit requests require the specified length and all requested changes")
    for action in data["actions"]:
        if action["action"] == "PRESERVE":
            continue
        active = {"ADD_LAYER": True, "REMOVE_LAYER": False}.get(action["action"])
        if active is None or changes.get(action["scope"]) is not active:
            raise PlanError("explicit requests authorize only the named layer changes")
    target = project(state, plan)
    expected_layers = tuple(replace(layer, active=changes[layer.name]) if layer.name in changes else layer
                            for layer in state.layers)
    if (Decimal(str(target.tempo_bpm)) != expected_bpm or target.layers != expected_layers
            or target.playing != state.playing):
        raise PlanError("plan must apply the exact requested tempo and layer switches while keeping playback")


def compile_jev_request(events, state, plan_id, *, tempo=None, layer_changes=None, bars=None):
    request = explicit_request(events, state=state)
    if request is None:
        return None
    if reason := request.refusal(state):
        return request_hold(events, state, plan_id, reason)
    expected_tempo = (request.tempo.mode, request.tempo.value) if request.tempo else (
        ("keep", None) if request.keep_tempo else None)
    if tempo is not None:
        if not isinstance(tempo, (tuple, list)) or len(tempo) != 2:
            return None
        mode, value = tempo
        try:
            tempo = (mode, Decimal(str(value)) if value is not None else None)
        except InvalidOperation:
            return None
        if tempo[1] is not None and not tempo[1].is_finite():
            return None
    layers = {} if layer_changes is None else layer_changes
    if (tempo != expected_tempo or bars != request.bars or type(bars) not in {type(None), int}
            or not isinstance(layers, dict) or any(type(value) is not bool for value in layers.values())
            or layers != dict(request.layer_changes)):
        return None
    changes = [(layer, active) for layer, active in request.layer_changes if state.layer(layer).active != active]
    preserve = ["key", "playing", *(f"layer:{layer.name}" for layer in state.layers
                                    if layer.name not in {name for name, _ in changes})]
    if request.tempo is None:
        preserve.append("tempo")
    if all(layer != "drums" for layer, _ in changes):
        preserve.append("groove")
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    actions = [{"action": "ADD_LAYER" if active else "REMOVE_LAYER", "scope": layer, "strength": .5, "parameters": {}}
               for layer, active in changes]
    transport = {"tempo_bpm": float(request.tempo.target(state))} if request.tempo else {}
    plan = make_plan(state, events, plan_id, "FOLLOW", actions, preserve, transport,
                     bars if bars is not None else 4, "执行明确的速度、声部与段落指令。", [])
    validate_request_plan(request, state, plan)
    return plan
