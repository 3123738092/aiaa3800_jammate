"""Conservative, deterministic SPEC baseline. This is not an LLM/NLU model.

Only complete clauses in the documented grammar execute. Original text lives
in InputEvent; unsupported or contradictory clauses produce an explicit hold.
"""
from __future__ import annotations

import math
import re
import uuid

from jammate.contracts.band_plan import BandPlan
from jammate.state import JamState
from .plans import make_plan

LAYERS = ("drums", "bass", "keys", "lead")
SCOPES = {"鼓": "drums", "贝斯": "bass", "键盘": "keys", "钢琴": "keys", "主旋律": "lead", "吉他": "guitar",
          **{name: name for name in (*LAYERS, "guitar", "band")}}
GRAMMAR = {
    "keep": r"这个不错|不错|保持|保持这样|保留|刚才那句保留|就这样|就这个|keep(?: it)?|sounds good",
    "build": r"再炸一点|再?猛一点|更猛(?:一点)?|更有力|渐强|推上去|build|more powerful",
    "simplify": r"太满了|留点空间|留空间|收一点|少一点|简化(?:一点)?|simplify|less busy",
    "stop": r"停|停止|结束|stop",
    "start": r"继续|恢复|开始|start|resume|continue",
    "faster": r"加速|快一点|快点|faster",
    "slower": r"减速|慢一点|慢点|slower",
    "tempo": r"速度不变|速度保持|别变速度|keep tempo",
    "groove": r"节奏保持|节奏别变|keep groove",
    "vary": r"变奏|变化一下|vary",
    "idea": r"你来|来一段|给个想法|new idea|your turn",
    "add": r"加|加上|加入|加回来|add",
    "remove": r"去掉|移除|remove",
}


def _clauses(text):
    for clause in re.split(r"[，,。.!！?？;；\n]|\band\b", text.lower().strip()):
        clause = clause.strip()
        if not clause:
            continue
        original, scope, negative = clause, "band", False
        fixed = next((k for k in ("tempo", "groove") if re.fullmatch(GRAMMAR[k], clause)), None)
        if fixed:
            yield original, scope, fixed, False
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
    def decide(self, events, state: JamState, *, plan_id: str | None = None, history=()) -> BandPlan:
        events = tuple(events)
        if not events or any(e.session_id != state.session_id for e in events):
            raise ValueError("Agent requires an accepted, nonempty session batch")
        plan_id = plan_id or f"plan_{uuid.uuid4().hex}"
        actions, transport, unresolved, reasons = [], {}, [], []
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
            if command is None or scope == "guitar":
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
                    preserve.extend(f"layer:{name}" for name in (LAYERS if scope == "band" else (scope,)))
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
                    actions.append(_action("ADD_LAYER" if command == "add" else "REMOVE_LAYER", scope))
            elif command in {"faster", "slower"}:
                bpm = max(60., min(180., state.tempo_bpm + (8 if command == "faster" else -8)))
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
                    actions.append(_action("ECHO_MOTIF", "lead", motif_id=motif_id))
                    preserve = [p for p in preserve if not p.startswith("motif:")]
                    strategy = "RESPOND"
                    reasons.append("Echo the latest observed phrase without removing its leading rest")
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
            if idle and not actions and not commands and not state.interaction.awaiting_user:
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
        if bars > 16:
            unresolved.append("Phrase exceeds the baseline 16-bar response limit")
        if sum(a["action"].endswith("MOTIF") for a in actions) > 1:
            unresolved.append("Conflicting motif requests")
        for left in actions:
            for right in actions:
                if {left["action"], right["action"]} in ({"BUILD", "SIMPLIFY"}, {"ADD_LAYER", "REMOVE_LAYER"}) and (left["scope"] == right["scope"] or "band" in {left["scope"], right["scope"]}):
                    unresolved.append("Contradictory actions on the same layer")
        # Explicit instrument scope keeps other layers unchanged unless another action touches them.
        modified = [a for a in actions if a["action"] != "PRESERVE"]
        if modified and all(a["scope"] in LAYERS for a in modified):
            preserve.extend(f"layer:{name}" for name in LAYERS if name not in {a["scope"] for a in modified})
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
