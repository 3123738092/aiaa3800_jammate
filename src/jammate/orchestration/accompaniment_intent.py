"""Apply explicit accompaniment edits using the score and settings actually played."""

from dataclasses import replace
from math import ceil
import re

from jammate.contracts.band_plan import MAX_PLAN_BARS

from .explicit_request import request_hold
from .plans import make_plan
from .tempo_intent import speech_clauses


_KEEP = r"(?:保持|保留|继续弹)(?:(?:刚才|上一段|刚刚)的)?(?:旋律|主旋律|那句|那段旋律)"
_THIN = r"(?:(?:把|让)?伴奏(?:减薄|简化|稀疏一点|少一点)|(?:减薄|简化)伴奏)"
_KEEP_AND_THIN = re.compile(
    rf"(?:{_KEEP}\s*(?:并|再|然后)?\s*{_THIN}|{_THIN}\s*(?:但|同时)?\s*{_KEEP}"
    r"|keep (?:the )?(?:previous |last )?melody\s*(?:and )?(?:thin|simplify) (?:the )?(?:backing|accompaniment))"
)
_RESTORE = re.compile(r"(?:(?:恢复|回到)(?:(?:刚才|刚刚|原来|之前)的|原)(?:干净的?)?伴奏"
                      r"|(?:restore|go back to) (?:the )?(?:previous|original) (?:backing|accompaniment))")
_PRESERVE = ["tempo", "key", "progression", "playing"]


def accompaniment_plan(request, plan_id):
    text = " ".join(speech_clauses(request.events))
    if not request.state.playing:
        return None
    if _KEEP_AND_THIN.fullmatch(text):
        return _thin_with_phrase(request, plan_id)
    if _RESTORE.fullmatch(text):
        return _restore(request, plan_id)
    return None


def _thin_with_phrase(request, plan_id):
    state, events = request.state, request.events
    if state.interaction.user_active:
        return request_hold(events, state, plan_id, "等你弹完这句，就能复奏并减薄伴奏。")
    phrase = next((m for m in reversed(state.motifs)
                   if tuple(replace(n, instrument="lead") for n in m.notes) == state.last_ai_motif), None)
    if phrase is None:
        return request_hold(events, state, plan_id, "当前还没有已演奏的旋律可复奏。")
    bars = ceil(max(n.end for n in phrase.notes) / state.beats_per_bar)
    if bars > MAX_PLAN_BARS:
        return request_hold(events, state, plan_id, "上一段旋律太长，请指定要复奏的片段。")
    actions = [{"action": "SIMPLIFY", "scope": "band", "strength": .5, "parameters": {}}]
    lead = state.layer("lead")
    if lead.level == 0:
        return request_hold(events, state, plan_id, "主旋律音量为零。调高后可以复奏。")
    if not lead.active:
        actions.append({"action": "ADD_LAYER", "scope": "lead", "strength": .5, "parameters": {}})
    actions.append({"action": "ECHO_MOTIF", "scope": "lead", "strength": .5,
                    "parameters": {"motif_id": phrase.motif_id}})
    return make_plan(state, events, plan_id, "RESPOND", actions, _PRESERVE, {}, bars,
                     "复奏刚才实际弹过的旋律，并减薄伴奏。", [])


def _restore(request, plan_id):
    state, events = request.state, request.events
    actions = []
    seen = set()
    for turn in reversed(request.history):
        applied = next((item["plan"] for item in turn["plans"] if item["status"] == "applied"), None)
        if applied is None or turn["state_after"] is None:
            continue
        before, after = ({part["name"]: part for part in turn[field]["arrangement"]["layers"]}
                         for field in ("state_before", "state_after"))
        simplified = [action["scope"] for action in applied["actions"] if action["action"] == "SIMPLIFY"]
        for part in state.layers:
            name = part.name
            if name in seen or name not in before or name not in after:
                continue
            old, new = before[name], after[name]
            if name == "lead" and old["active"] != new["active"]:
                seen.add(name)
                played_phrase = any(action["action"] in {"ECHO_MOTIF", "VARY_MOTIF"} for action in applied["actions"])
                same_mix = (part.level, part.density) == (new["level"], new["density"])
                if played_phrase and part.active and not old["active"] and same_mix:
                    actions.append({"action": "REMOVE_LAYER", "scope": name, "strength": .5, "parameters": {}})
            elif old["density"] != new["density"]:
                seen.add(name)
                if ("band" in simplified or name in simplified) and part.density == new["density"]:
                    actions.append({"action": "ADJUST_FEEL", "scope": name, "strength": .5,
                                    "parameters": {"density": old["density"]}})
    if not actions:
        return request_hold(events, state, plan_id, "最近没有可以恢复的伴奏调整。")
    changed = {action["scope"] for action in actions}
    preserve = [*_PRESERVE, *(f"layer:{part.name}" for part in state.layers if part.name not in changed)]
    if "drums" not in changed:
        preserve.append("groove")
    return make_plan(state, events, plan_id, "FOLLOW", actions, preserve, {}, 4, "恢复刚才的伴奏设置。", [])
