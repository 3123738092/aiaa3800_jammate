"""Ground explicit tempo commands in the submitted text before executing a plan."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from jammate.contracts import PlanError
from jammate.state import MAX_TEMPO_BPM, MIN_TEMPO_BPM
from .plans import make_plan


_NUMBER = r"(\d{1,3}(?:\.\d{1,3})?)"
_UNIT = r"(?:bpm|拍每分钟)"
_SET = re.compile(
    rf"(?:(?:请|只)?\s*(?:把|将)?\s*(?:速度|bpm)\s*(?:调整到|调到|调至|设成|设到|设为|改到|改为|改成)\s*{_NUMBER}(?:\s*{_UNIT})?"
    rf"|(?:请\s*)?(?:set(?:\s+the)?\s+(?:tempo|speed)(?:\s+to)?|设到|设为|改成)\s*{_NUMBER}\s*{_UNIT})"
)
_DOWN = re.compile(rf"(?:(?:从(?:当前速度|现在这个数))?\s*(?:再)?\s*(?:减(?:慢)?|降(?:低)?|慢)|(?:reduce|decrease)(?:\s+tempo)?\s+by)\s*{_NUMBER}\s*{_UNIT}")
_UP = re.compile(rf"(?:(?:从(?:当前速度|现在这个数))?\s*(?:再)?\s*(?:加(?:快)?|升(?:高)?|快)|(?:increase|raise)(?:\s+tempo)?\s+by)\s*{_NUMBER}\s*{_UNIT}")
_KEEP = {"只改速度", "其他都不动", "其他全部保持", "其他保持", "其他照旧", "编制照旧", "配器别碰",
         "保持其他不变", "除此之外都别动", "keep everything else", "keep everything else unchanged"}
_CONDITIONAL = re.compile(rf"(?:如果)?\s*(?:能做到|能设到|支持)\s*{_NUMBER}\s*{_UNIT}\s*就(?:用|设为)\s*{_NUMBER}(?:\s*{_UNIT})?")
_ON_FAILURE = re.compile(
    r"(?:(?:这个数)?(?:超出范围|超范围|不支持|做不到)(?:的?话|就|时)?(?:请)?告诉我"
    r"|(?:别|不要)(?:自行)?(?:换成别的数|替换数值|往下一段走|继续生成|继续|生成下一段)"
    r"|(?:也)?(?:别|不要)(?:往下一段走|继续生成|生成下一段))"
)


@dataclass(frozen=True)
class TempoIntent:
    mode: str
    value: Decimal

    def target(self, state):
        if self.mode == "set":
            return self.value
        current = Decimal(str(state.tempo_bpm))
        return current + self.value if self.mode == "up" else current - self.value

    def refusal(self, state):
        target = self.target(state)
        if MIN_TEMPO_BPM <= target <= MAX_TEMPO_BPM:
            return None
        return f"目标速度 {target} BPM 超出 {MIN_TEMPO_BPM}–{MAX_TEMPO_BPM} BPM 范围。"


def speech_clauses(events):
    if len(events) != 1 or events[0].event_type != "speech":
        return []
    text = unicodedata.normalize("NFKC", events[0].payload["text"]).lower()
    return [part.strip().rstrip(".!?") for part in re.split(r"[，,；;。！？!?\n]", text) if part.strip()]


def tempo_clause(text):
    for mode, pattern in (("set", _SET), ("up", _UP), ("down", _DOWN)):
        if match := pattern.fullmatch(text):
            return TempoIntent(mode, Decimal(next(value for value in match.groups() if value is not None)))
    return None


def explicit_tempo(events):
    """Recognize only complete, narrowly scoped commands with no unparsed clauses."""
    clauses = speech_clauses(events)
    if not clauses:
        return None
    conditional = _CONDITIONAL.fullmatch(clauses[0])
    if (conditional and len(clauses) > 1 and Decimal(conditional[1]) == Decimal(conditional[2])
            and all(_ON_FAILURE.fullmatch(item) for item in clauses[1:])):
        return TempoIntent("set", Decimal(conditional[1]))
    requests = [item for item in clauses if item not in _KEEP]
    if len(requests) != 1:
        return None
    return tempo_clause(requests[0])


def validate_tempo_plan(intent, state, plan):
    data = plan.to_dict()
    transport = data["transport"]
    if (Decimal(str(transport.get("tempo_bpm", state.tempo_bpm))) != intent.target(state)
            or transport.get("playing", state.playing) != state.playing
            or any(action["action"] != "PRESERVE" for action in data["actions"])
            or data["unresolved"]):
        raise PlanError("explicit tempo command requires its exact target and unchanged playback and layers")


def compile_jev_tempo(events, state, plan_id, *, mode, value, bars=4):
    """Bind a Jev candidate to a complete explicit request or abstain."""
    intent = explicit_tempo(events)
    if intent is None or mode != intent.mode:
        return None
    try:
        candidate = Decimal(str(value))
    except InvalidOperation:
        return None
    if not candidate.is_finite() or candidate != intent.value:
        return None
    if reason := intent.refusal(state):
        return tempo_hold(events, state, plan_id, reason)
    keep = ["key", "groove", "playing", *(f"layer:{layer.name}" for layer in state.layers)]
    if state.current_motif_id:
        keep.append(f"motif:{state.current_motif_id}")
    return make_plan(state, events, plan_id, "FOLLOW", [], keep, {"tempo_bpm": float(intent.target(state))},
                     bars, "按明确的速度指令调整。", [])


def tempo_hold(events, state, plan_id, reason):
    return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                     "保持当前状态，等待可执行的速度。", [reason])
