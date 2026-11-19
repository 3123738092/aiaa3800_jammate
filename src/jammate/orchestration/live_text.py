"""Classify a live text turn and compile strictly scoped, verifiable edits."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from jammate.contracts import InputEvent, PlanError
from jammate.instruments import added_program
from .agent import SCOPES
from .explicit_request import compile_jev_request, explicit_request
from .key_intent import explicit_key_plan
from .llm import available_instruments, validate_plan
from .plans import make_plan
from .progression_intent import explicit_progression_plan
from .tempo_intent import speech_clauses


JEV_TIMEOUT = 3.0
JEV_URL = "https://api.ohmygpt.com/v1/systemone"
MIN_SCORE = .90
_OPS = {
    "quieter": "Turn down the volume of an existing named layer.",
    "louder": "Turn up the volume of an existing named layer.",
    "thinner": "Use fewer notes on an existing named accompaniment layer.",
    "mute": "Mute an existing named layer.", "restore": "Restore a previously muted named layer.",
    "add_named": "Introduce exactly the instrument named by the user.",
    "drum_offbeat": "Explicitly put the drums on the offbeats.",
    "tempo": "Change or preserve tempo.", "length": "Choose how many bars to play.",
    "transport": "Start or stop the whole jam.", "key": "Change the musical key.",
    "harmony": "Change a chord or chord progression.", "style": "Interpret a style or atmosphere.",
    "rhythm": "Change rhythm in a way that needs interpretation.",
    "choose_instrument": "Choose, replace, or design a set of musical instruments.",
    "compound": "Multiple distinct requested changes.", "unclear": "Other or insufficient evidence.",
}
_ALIASES = "|".join(rf"\b{re.escape(alias)}\b" if alias.isascii() else re.escape(alias)
                    for alias in sorted(SCOPES, key=len, reverse=True) if alias != "band")
_LAYERS = rf"(?:{_ALIASES})(?:\s*(?:和|与|及|、|and)\s*(?:{_ALIASES}))*"
_KEEP = re.compile(
    rf"(?:(?:keep|leave)\s+(?:the\s+)?(?:{_LAYERS}|everything else|other layers)"
    rf"\s+(?:unchanged|as (?:it is|they are))|(?:保持|保留)(?:{_LAYERS}|速度|原速|节奏)"
    rf"|{_LAYERS}(?:照旧|照现在这样|别动|保持不变|不变)|(?:其他|其余)(?:声部)?(?:照旧|不变)|速度不变)"
)
_UNSAFE_DIRECT = re.compile(
    r"\d|不要|不用|先别|别|不必|原来那个|刚才|同时|而且|并且|但|然后|再把|或者|还是|改口|不对|"
    r"\b(?:not|never|don't|without|except|instead|and|or|but|actually)\b"
)
_EVIDENCE = {
    "quieter": r"轻|小声|安静|音量|压低|quieter|volume|turn down|softer",
    "louder": r"响|大声|音量|有力|猛|louder|volume|turn up|stronger",
    "thinner": r"少|稀|空隙|留.{0,4}空间|简化|less often|fewer|gaps|sparser|thin out|less busy",
    "mute": r"静音|停|关|去掉|mute|stop|disable|take out",
    "restore": r"恢复|加回来|解除静音|回来|unmute|restore|bring back|resume",
    "add_named": r"新增|加入|加上|加进来|添加|加|add|bring in|introduce",
    "drum_offbeat": r"反拍|\boff[- ]?beats?\b",
}
_NEXT_PREFIX = re.compile(r"^(?:在)?下(?:一)?个?小节(?!后|之后|结束)(?:开始|起)?[，,：:\s]*")
_STOP = {"停", "停奏", "停止", "停止演奏", "停止伴奏", "stop", "stop playing"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _choice(instruction, options):
    return {"type": "choice", "instructions": instruction, "criteria": options}


class JevLiveTagger:
    def __init__(self, key):
        self.key = key

    def __call__(self, text, state, catalogue):
        targets = {layer.name: f"Existing layer {layer.name}." for layer in state.layers}
        normalized = text.casefold()
        named_programs = {added_program(SCOPES[name]) for name in re.findall(_ALIASES, normalized)}
        targets.update({item["name"]: f"Available instrument {item['label']}." for item in catalogue
                        if added_program(item["name"]) in named_programs or item["label"].casefold() in normalized})
        targets["none"] = "No exact layer or explicitly named available instrument."
        questions = {
            "operation": _choice("Which operation covers the whole request? Preservation clauses do not count as another operation.", _OPS),
            "target": _choice("Which one target does the user explicitly name for this operation? Ignore names in keep clauses.", targets),
            "timing": _choice("Does the user require an exact playback time or position?", {
                "flexible": "No exact time stated.", "fixed": "Exact time or bar required.", "unclear": "Uncertain timing.",
            }),
            "coverage": _choice(
                "A named layer plus a small relative change (such as less busy or quieter), explicit offbeats, "
                "or adding a named instrument is enough for one action. Clauses keeping other parts are allowed. "
                "Is there any additional requested change or important unresolved choice?", {
                "exact": "No additional requested change. One action with compatible keep clauses.",
                "design": "Another change, stylistic interpretation, or selecting unnamed material is necessary.",
                "unclear": "Conflicting, conditional, ambiguous or unsupported.",
            }),
            "constraints": _choice("Check protected material, negation, corrections and conflicting clauses.", {
                "none": "No restriction beyond the instruction itself.",
                "preserve": "Simple compatible request to keep other parts or the pace.",
                "unclear": "A negation, correction, conflict or unresolved constraint needs fuller interpretation.",
            }),
        }
        request = Request(
            JEV_URL, json.dumps({"model": "jev-latest", "questions": questions, "state": {
                "text": text, "layers": [{"name": layer.name, "active": layer.active} for layer in state.layers],
                "tempo_bpm": state.tempo_bpm,
            }}, ensure_ascii=False).encode(),
            {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with build_opener(_NoRedirect).open(request, timeout=JEV_TIMEOUT) as response:
                result = json.load(response)
            answers = result["answers"]
            selected = {}
            for name, question in questions.items():
                choice, score = answers[name]["choice"], float(answers[name]["confidence"])
                if choice in question["criteria"] and 0 <= score <= 1:
                    selected[name] = choice, score
            return selected
        except (URLError, OSError, ValueError, TypeError, KeyError):
            return {}


def _exact_plan(events, state, plan_id):
    if len(events) == 1 and events[0].event_type == "speech":
        event = events[0].to_dict()
        text = _NEXT_PREFIX.sub("", event["payload"]["text"], count=1)
        if text and text != event["payload"]["text"]:
            event["payload"]["text"] = text
            events = (InputEvent.from_dict(event),)
    clauses = speech_clauses(events)
    if len(clauses) == 1 and clauses[0] in _STOP:
        return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "progression"],
                         {"playing": False}, 1, "停止演奏。", [])
    plan = explicit_key_plan(events, state, plan_id) or explicit_progression_plan(events, state, plan_id)
    if plan:
        return plan
    parsed = explicit_request(events, state=state)
    if parsed is None:
        return None
    tempo = (parsed.tempo.mode, parsed.tempo.value) if parsed.tempo else ("keep", None) if parsed.keep_tempo else None
    return compile_jev_request(events, state, plan_id, tempo=tempo, layer_changes=dict(parsed.layer_changes), bars=parsed.bars)


def _tagged_plan(events, state, plan_id, tags, catalogue):
    if set(tags) != {"operation", "target", "coverage", "timing", "constraints"}:
        return None
    if (not all(MIN_SCORE <= tags[name][1] <= 1.0 for name in ("operation", "target"))
            or tags["coverage"][0] != "exact" or tags["timing"][0] != "flexible"
            or tags["constraints"][0] not in {"none", "preserve"}):
        return None
    operation, target = tags["operation"][0], tags["target"][0]
    if operation not in _EVIDENCE:
        return None
    clauses = speech_clauses(events)
    kept = [clause for clause in clauses if _KEEP.fullmatch(clause)]
    action_clauses = [clause for clause in clauses if clause not in kept]
    if len(action_clauses) != 1:
        return None
    text = action_clauses[0]
    if _UNSAFE_DIRECT.search(text) or not re.search(_EVIDENCE[operation], text):
        return None
    named = {SCOPES[label] for label in re.findall(_ALIASES, text)}
    protected = {SCOPES[label] for clause in kept for label in re.findall(_ALIASES, clause)}
    if target in protected or (target == "drums" and operation not in {"quieter", "louder"}
                               and any("节奏" in clause for clause in kept)):
        return None
    if operation == "add_named":
        item = next((item for item in catalogue if item["name"] == target), None)
        if item is None:
            return None
        label = item["label"].casefold()
        # Also allow a recognized local alias for a named local timbre. A GM ID
        # must be anchored to its SoundFont label before it is executable.
        matching_aliases = {name for name in named if name == target or (
            added_program(name) is not None and added_program(name) == added_program(target)
        )}
        if named - matching_aliases or (
            not matching_aliases and not re.search(rf"(?:^|[\s，。]){re.escape(label)}(?:$|[\s，。])", text)
        ):
            return None
    elif named != {target} or target not in {layer.name for layer in state.layers}:
        return None
    elif operation == "thinner" and target == "lead":
        return None
    elif operation == "drum_offbeat" and (target != "drums" or state.rhythm is not None):
        return None

    name, scope, parameters = {
        "quieter": ("ADJUST_FEEL", target, {"level_delta": -.1}),
        "louder": ("ADJUST_FEEL", target, {"level_delta": .1}),
        "thinner": ("SIMPLIFY", target, {"duration_bars": 4}),
        "mute": ("REMOVE_LAYER", target, {}),
        "restore": ("ADD_LAYER", target, {}),
        "add_named": ("INTRODUCE_LAYER", target, {}),
        "drum_offbeat": ("SET_TEXTURE", "band", {**asdict(state.texture), "drums": "offbeat"}),
    }[operation]
    preserve = ["tempo", "key", "progression", "playing"]
    if target != "drums" or operation in {"quieter", "louder"}:
        preserve.append("groove")
    preserve.extend(f"layer:{layer.name}" for layer in state.layers if layer.name not in {target, "lead"})
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    return make_plan(state, events, plan_id, "FOLLOW",
                     [{"action": name, "scope": scope, "strength": .5, "parameters": parameters}],
                     preserve, {}, 4, "按已核对的声部和动作更新伴奏。", [])


class LiveTextAgent:
    def __init__(self, primary, tagger=None):
        self.primary, self.tagger = primary, tagger
        self.last_trace = {}

    def decide(self, events, state, *, plan_id=None, history=()):
        events = tuple(events)
        plan_id = plan_id or f"plan_{uuid4().hex}"
        tags = {}
        plan = _exact_plan(events, state, plan_id) if state.playing and not state.interaction.user_active else None
        if plan is not None:
            validate_plan(plan, events, state)
            if not plan.to_dict()["unresolved"]:
                self.primary.execution_validator(state, plan)
            self.last_trace = {"source": "local_explicit"}
            return plan
        if self.tagger and len(events) == 1 and events[0].event_type == "speech":
            catalogue = available_instruments(state, self.primary.soundfont_programs)
            tags = self.tagger(events[0].payload["text"], state, catalogue)
            if state.playing and not state.interaction.user_active:
                try:
                    plan = _tagged_plan(events, state, plan_id, tags, catalogue)
                    if plan is not None:
                        validate_plan(plan, events, state)
                        self.primary.execution_validator(state, plan)
                        self.last_trace = {"source": "jev_direct", "tags": tags}
                        return plan
                except PlanError:
                    pass
        try:
            return self.primary.decide(events, state, plan_id=plan_id, history=history, intent_hints=tags)
        finally:
            self.last_trace = {**self.primary.last_trace, "tags": tags}
