"""Conservative, deterministic SPEC baseline. This is not an LLM/NLU model.

Only complete clauses in the documented grammar execute. Original text lives
in InputEvent; unsupported or contradictory clauses produce an explicit hold.
"""
from __future__ import annotations

import math
import re
import uuid

from jammate.contracts.band_plan import MAX_PLAN_BARS, BandPlan
from jammate.instruments import ADDED_INSTRUMENTS, INSTRUMENT_ALIASES
from jammate.interaction import CORE_LAYER_NAMES, LAYER_NAMES
from jammate.state import MAX_TEMPO_BPM, MIN_TEMPO_BPM, JamState
from .addition_intent import unspecified_instrument_plan
from .explicit_request import explicit_mix_plan
from .initiative import initiative_plan
from .key_intent import explicit_key_plan
from .progression_intent import explicit_progression_plan
from .plans import make_plan

SCOPES = {"鼓": "drums", "贝斯": "bass", "键盘": "keys", "钢琴": "keys", "主旋律": "lead",
          **{name: name for name in (*CORE_LAYER_NAMES, "band")},
          **dict(sorted(INSTRUMENT_ALIASES.items(), key=lambda item: -len(item[0])))}
GRAMMAR = {
    "keep": r"这个不错|不错|保持|保持这样|保持原样|保留|刚才那句保留|就这样|就这个|就这个感觉继续|nice|keep(?: it)?|sounds good",
    "build": r"再炸一点|再?猛一点|再强一点|更猛(?:一点)?|更有力|渐强|推上去|build|more powerful",
    "simplify": r"太满了|(?:给我)?留点空间|留空间|收一点|少一点|简化(?:一点)?|simplify|less busy",
    "stop": r"停|停止|停止演奏|停止伴奏|结束|stop(?: playing)?",
    "start": r"继续|恢复|开始|start|resume|continue",
    "faster": r"加速|(?:稍微)?快一点|快点|faster",
    "slower": r"减速|(?:速度)?慢一点|慢点|slower",
    "tempo": r"速度不变|速度保持|别变速度|keep tempo",
    "groove": r"节奏保持|节奏别变|keep(?: the same)? groove",
    "vary": r"变奏|变化一下|回应\s*ai\s*(?:刚才|刚刚)(?:提出|演奏)的\s*riff|vary",
    "idea": r"你来|来一段|给个想法|你来一句新的\s*riff|new idea|your turn",
    "add": r"加|加上|加入|加回来|add",
    "add_new_instrument": r"加一个(?:其他|别的|新)乐器(?:进来)?|add another instrument",
    "remove": r"去掉|移除|remove",
}


def _clauses(text):
    text = re.sub(r"^\s*等一下[，,]\s*(?=保持原样|保持这样|就这样)", "", text)
    text = re.sub(r"([，,])\s*我想自己唱[。.!！]?\s*$", "", text) if "留点空间" in text else text
    for clause in re.split(r"[，,。.!！?？;；\n]|\band\b", text.lower().strip()):
        clause = clause.strip()
        if not clause:
            continue
        original, scope, negative = clause, "band", False
        fixed = next((k for k in ("tempo", "groove") if re.fullmatch(GRAMMAR[k], clause)), None)
        if fixed:
            yield original, scope, fixed, False
            continue
        if re.fullmatch(r"(?:保持|保留)(?:这个|当前|这段)?(?:主)?旋律", clause):
            yield original, "lead", "keep", False
            continue
        # Negation may precede a scope (不要鼓加上) or follow it (鼓不要加上).
        for _ in range(2):
            neg = re.match(r"^(?:不要|别|do not\s+|don't\s+)", clause)
            if neg:
                negative, clause = True, clause[neg.end():].strip()
            for alias, name in SCOPES.items():
                if clause.startswith(alias) and (not alias.isascii() or len(clause) == len(alias) or clause[len(alias)].isspace()):
                    scope, clause = name, clause[len(alias):].strip()
                    break
        match = next((name for name, pattern in GRAMMAR.items() if re.fullmatch(pattern, clause)), None)
        # Common imperative ordering: 加上贝斯 / remove bass.
        if match is None and scope == "band":
            for alias, name in SCOPES.items():
                if clause.endswith(alias):
                    head = clause[:-len(alias)].strip()
                    match = next((key for key in ("add", "remove") if re.fullmatch(GRAMMAR[key], head)), None)
                    if match:
                        scope = name
                        break
        yield original, scope, match, negative


def _action(name, scope="band", strength=.5, **parameters):
    return {"action": name, "scope": scope, "strength": strength, "parameters": parameters}


class RuleAgent:
    def decide(self, events, state: JamState, *, plan_id: str | None = None, history=(),
               vary_input=False, direct_bass_enabled=False) -> BandPlan:
        events = tuple(events)
        if not events or any(e.session_id != state.session_id for e in events):
            raise ValueError("Agent requires an accepted, nonempty session batch")
        plan_id = plan_id or f"plan_{uuid.uuid4().hex}"
        if plan := (initiative_plan(events, state, plan_id) or explicit_key_plan(events, state, plan_id)
                    or explicit_progression_plan(events, state, plan_id)
                    or explicit_mix_plan(events, state, plan_id)
                    or unspecified_instrument_plan(events, state, plan_id)):
            return plan
        actions, transport, unresolved, reasons = [], {}, [], []
        existing_layers = tuple(layer.name for layer in state.layers)
        preserve = ["tempo", "key"]
        if state.current_motif_id:
            preserve.append(f"motif:{state.current_motif_id}")
        strategy, bars = "FOLLOW", 4
        commands = []
        for event in events:
            if event.event_type == "speech":
                commands.extend(_clauses(event.payload["text"]))
        controls = [e.payload for e in events if e.event_type == "session_control"]
        stopping = any(c["command"] == "stop" for c in controls) or any(k == "stop" and scope == "band" and not neg for _, scope, k, neg in commands)
        if stopping:
            return self._plan(state, events, plan_id, "LISTEN", [], ["tempo", "key"], {"playing": False}, 1,
                              "Explicit stop takes priority over all musical requests", [])
        for original, scope, command, negative in commands:
            if command == "add_new_instrument" and scope == "band" and "guitar" not in existing_layers:
                scope, command = "guitar", "add"
            missing_instrument = scope in ADDED_INSTRUMENTS and scope not in existing_layers
            if command is None or command == "add_new_instrument" or (missing_instrument and (command != "add" or negative)):
                unresolved.append(f"Unsupported clause: {original}")
                continue
            if scope != "band" and command not in {"keep", "build", "simplify", "add", "remove", "stop", "start"}:
                unresolved.append(f"Unsupported instrument-scoped request: {original}")
                continue
            if negative:
                if command in {"stop", "start"}:
                    preserve.append("playing" if scope == "band" else f"layer:{scope}")
                elif command in {"faster", "slower"}:
                    preserve.append("tempo")
                elif command in {"build", "simplify", "add", "remove"}:
                    preserve.extend(f"layer:{name}" for name in (existing_layers if scope == "band" else (scope,)))
                else:
                    unresolved.append(f"Unsupported negation: {original}")
                reasons.append(f"Respect negation: {original}")
                continue
            reasons.append(original)
            if command == "keep":
                actions.append(_action("PRESERVE", scope))
            elif command in {"tempo", "groove"}:
                preserve.append(command)
            elif command == "start":
                if scope == "band":
                    transport["playing"] = True
                else:
                    actions.append(_action("ADD_LAYER", scope))
            elif command == "stop":
                actions.append(_action("REMOVE_LAYER", scope))
            elif command in {"build", "simplify"}:
                actions.append(_action("BUILD" if command == "build" else "SIMPLIFY", scope, duration_bars=4))
            elif command in {"add", "remove"}:
                if scope == "band":
                    unresolved.append("Layer command requires an instrument")
                else:
                    name = "ADD_LAYER" if command == "add" else "REMOVE_LAYER"
                    if command == "add" and scope not in existing_layers:
                        name = "INTRODUCE_LAYER"
                    actions.append(_action(name, scope))
            elif command in {"faster", "slower"}:
                bpm = max(MIN_TEMPO_BPM, min(MAX_TEMPO_BPM, state.tempo_bpm + (8 if command == "faster" else -8)))
                if "tempo_bpm" in transport and transport["tempo_bpm"] != bpm:
                    unresolved.append("Conflicting tempo requests")
                transport["tempo_bpm"] = bpm
                preserve = [p for p in preserve if p != "tempo"]
            elif command == "vary":
                if not state.current_motif_id:
                    unresolved.append("No current motif to vary")
                else:
                    actions.append(_action("VARY_MOTIF", "lead", motif_id=state.current_motif_id))
                    preserve = [p for p in preserve if not p.startswith("motif:")]
            elif command == "idea":
                actions.append(_action("INTRODUCE_MOTIF", "lead", motif_id=f"motif_{plan_id}"))
                preserve = [p for p in preserve if not p.startswith("motif:")]
                strategy = "INITIATE"
        # Positive requests cannot erase explicit negative/keep constraints from another clause.
        if any(k == "tempo" or (neg and k in {"faster", "slower"}) for _, _, k, neg in commands):
            preserve.append("tempo")
        if any(c["command"] == "start" for c in controls):
            transport["playing"] = True
        if not state.playing and not transport.get("playing"):
            return self._plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                              "Session is stopped; explicit start is required", unresolved)
        # A human currently performing owns the turn. Do not answer over them.
        if state.interaction.user_active:
            actions, strategy = [_action("SIMPLIFY", "band", .3, duration_bars=4)], "LISTEN"
            reasons.append("User is active: reduce accompaniment and listen")
        elif not unresolved:
            phrases = [e for e in events if e.event_type == "musical_phrase"]
            rhythms = [e for e in events if e.event_type == "rhythm_seed"]
            if phrases and not any(a["action"].endswith("MOTIF") for a in actions):
                motif_id = f"motif_{phrases[-1].event_id}"
                found = next((m for m in state.motifs if m.motif_id == motif_id), None)
                if found is not None:
                    action = "VARY_MOTIF" if vary_input else "ECHO_MOTIF"
                    actions.append(_action(action, "lead", motif_id=motif_id))
                    preserve = [p for p in preserve if not p.startswith("motif:")]
                    strategy = "RESPOND"
                    reasons.append("Develop a variation of the observed phrase" if vary_input else
                                   "Echo the latest observed phrase without removing its leading rest")
            if rhythms:
                event, payload = rhythms[-1], rhythms[-1].payload
                context = payload["rhythm"]
                if payload["status"] == "ready" and context["confidence"] >= .65:
                    actions.append(_action("ADJUST_FEEL", rhythm_event_id=event.event_id))
                    strategy = "RESPOND" if strategy != "INITIATE" else strategy
                    reasons.append("Adopt rhythmic onsets at the existing session tempo")
                    # Tempo changes require an explicit text/control decision in this baseline.
                else:
                    reasons.append("Rhythm is empty or below the 0.65 alignment threshold; retain groove")
            idle = any(c["command"] == "idle" and c["bars"] >= 2 for c in controls)
            if (idle and not actions and not commands and state.interaction.allow_initiative
                    and not state.interaction.awaiting_user and state.key is not None and state.layer("lead").active):
                actions.append(_action("INTRODUCE_MOTIF", "lead", motif_id=f"motif_{plan_id}"))
                preserve = [p for p in preserve if not p.startswith("motif:")]
                strategy = "INITIATE"
                reasons.append("Two idle bars: offer one short motif, then wait for the user")
            elif idle and state.interaction.awaiting_user:
                strategy = "LISTEN"
                reasons.append("Already offered an idea; wait for a human response")
        # Collapse repeated requests (太满了，留点空间) without doubling their magnitude.
        actions = [a for i, a in enumerate(actions) if a not in actions[:i]]
        for a in actions:
            if a["action"] in {"ECHO_MOTIF", "VARY_MOTIF"}:
                length = max(n.end for n in state.motif(a["parameters"]["motif_id"]).notes)
                bars = max(bars, math.ceil(length / 4))
        if bars > MAX_PLAN_BARS:
            unresolved.append(f"Phrase exceeds the {MAX_PLAN_BARS}-bar response limit")
        if sum(a["action"].endswith("MOTIF") for a in actions) > 1:
            unresolved.append("Conflicting motif requests")
        for left in actions:
            for right in actions:
                if {left["action"], right["action"]} in ({"BUILD", "SIMPLIFY"}, {"ADD_LAYER", "REMOVE_LAYER"}, {"INTRODUCE_LAYER", "REMOVE_LAYER"}) and (left["scope"] == right["scope"] or "band" in {left["scope"], right["scope"]}):
                    unresolved.append("Contradictory actions on the same layer")
        # Explicit instrument scope keeps other layers unchanged unless another action touches them.
        modified = [a for a in actions if a["action"] != "PRESERVE"]
        if modified and all(a["scope"] in LAYER_NAMES for a in modified):
            mutable_layers = {a["scope"] for a in modified}
            if direct_bass_enabled and state.position.next_bar == 0 and strategy == "INITIATE":
                mutable_layers.add("bass")
            preserve.extend(f"layer:{layer.name}" for layer in state.layers if layer.name not in mutable_layers)
        if unresolved:
            return self._plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                              "Hold current arrangement; request needs clarification", list(dict.fromkeys(unresolved)))
        plan = self._plan(state, events, plan_id, strategy, actions, preserve, transport, bars,
                          "; ".join(reasons) or "Retain current accompaniment", [])
        # Run the same semantic gate used for future model-produced plans.
        from .transitions import project
        from jammate.contracts.band_plan import PlanError
        try:
            project(state, plan)
        except PlanError as exc:
            return self._plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                              "Hold current arrangement; constraints conflict", [str(exc)])
        return plan

    @staticmethod
    def _plan(state, events, plan_id, strategy, actions, preserve, transport, bars, reason, unresolved):
        return make_plan(state, events, plan_id, strategy, actions, preserve, transport, bars, reason, unresolved)
