"""Compare three text decision paths using the same virtual Jam playback."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import unicodedata

from examples.text_benchmark.jev_probe import MODEL, choice, run_case
from jammate.contracts import PlanError
from jammate.orchestration.agent import SCOPES, RuleAgent
from jammate.orchestration.explicit_request import compile_jev_request, explicit_request
from jammate.orchestration.llm import validate_plan
from jammate.orchestration.plans import make_plan

from run import Replay, validate


FIXTURE = Path(__file__).with_name("rule_jev_cases.json")
LAYERS = ("drums", "bass", "keys", "lead")
SCOPED_DENSITY = ("drums", "bass", "keys")
MIN_CONFIDENCE = .90
ALIASES = {**SCOPES, "drum": "drums", "键盘手": "keys", "keyboard": "keys", "piano": "keys"}
ALIAS = "|".join(
    (rf"\b{re.escape(name)}\b" if name.isascii() else re.escape(name))
    for name in sorted(ALIASES, key=len, reverse=True)
)
LAYER_LIST = rf"(?:{ALIAS})(?:\s*(?:和|与|及|以及|、|and)\s*(?:{ALIAS}))*"
KEEP = re.compile(
    rf"(?:(?:keep|leave)\s+(?:the\s+)?(?:{LAYER_LIST}|other layers|everything else)"
    rf"\s+(?:unchanged|as (?:it is|they are))"
    rf"|(?:保持|保留){LAYER_LIST}"
    rf"|{LAYER_LIST}(?:照现在这样|保持不变|别动|不变|照旧)|其他(?:声部)?(?:不变|照旧))"
)
BLOCKED = re.compile(
    r"\d|不要|不用|别|不必|改成|改为|而且|同时|并且|但是|或者|但|"
    r"\b(?:not|never|don't|without|except|instead|and|or|but|actually)\b"
)


def explicit_plan(events, state, plan_id):
    request = explicit_request(events, state=state)
    if request is None:
        return None
    tempo = ("keep", None) if request.keep_tempo else None
    if request.tempo is not None:
        tempo = (request.tempo.mode, request.tempo.value)
    return compile_jev_request(
        events, state, plan_id, tempo=tempo, layer_changes=dict(request.layer_changes), bars=request.bars,
    )


def density_target(text):
    """Require one named action clause and only exact preservation clauses."""
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    clauses = [part.strip() for part in re.split(r"[，,。.!！?？;；\n]", normalized) if part.strip()]
    protects = [part for part in clauses if KEEP.fullmatch(part)]
    action = [part for part in clauses if part not in protects]
    if len(action) != 1 or BLOCKED.search(action[0]):
        return None
    targets = {ALIASES[name] for name in re.findall(ALIAS, action[0])}
    if len(targets) != 1:
        return None
    (target,) = targets
    protected = {ALIASES[name] for part in protects for name in re.findall(ALIAS, part)}
    return target if target in SCOPED_DENSITY and target not in protected else None


def ask_jev(text, state, key):
    questions = {
        "effect": choice(
            "Interpret the entire current user request. Clauses asking to preserve other layers are allowed. "
            "Select simplify only if exactly one named accompaniment layer should play fewer notes or "
            "leave more gaps across its part. Any extra requested operation requires a different route.",
            {
                "simplify": "One explicitly named accompaniment layer should have fewer attacks or a sparser part.",
                "other": "Volume, muting, pitch/register, timing of a specific hit, another change, or no change.",
                "defer": "Ambiguous, conditional, contradictory, or more than one substantive operation.",
            },
        ),
        "target": choice(
            "Which layer does the user ask to change? Ignore layers explicitly requested to stay the same.",
            {
                "drums": "Drum part only.", "bass": "Bass part only.", "keys": "Keyboard or piano part only.",
                "defer": "Other, missing or multiple targets.",
            },
        ),
    }
    case = {"text": text, "expected": {}}
    return run_case(case, key, body={
        "model": MODEL,
        "state": {"current_user_request": text, "tempo_bpm": state.tempo_bpm,
                  "active_layers": [layer.name for layer in state.layers if layer.active]},
        "questions": questions,
    })


def passes_confidence(answer):
    value = answer.get("confidence")
    return type(value) in {int, float} and math.isfinite(value) and MIN_CONFIDENCE <= value <= 1


def density_plan(events, state, plan_id, target):
    preserve = ["tempo", "key", "playing", *(f"layer:{layer.name}" for layer in state.layers if layer.name != target)]
    if target != "drums":
        preserve.append("groove")
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    plan = make_plan(
        state, events, plan_id, "FOLLOW",
        [{"action": "SIMPLIFY", "scope": target, "strength": .5, "parameters": {"duration_bars": 4}}],
        preserve, {}, 4, "The classifier selected an unquantified note-density reduction.", [],
    )
    return validate_plan(plan, events, state)


class ExperimentalAgent:
    def __init__(self, key=None):
        self.rule = RuleAgent()
        self.key = key
        self.last_trace = {}

    def decide(self, events, state, *, plan_id, history=()):
        self.last_trace = {"source": "rule"}
        single_text = len(events) == 1 and events[0].event_type == "speech"
        eligible = single_text and state.playing and not state.interaction.user_active
        if eligible and (plan := explicit_plan(events, state, plan_id)) is not None:
            self.last_trace["source"] = "local_explicit"
            return validate_plan(plan, events, state)
        original = self.rule.decide(events, state, plan_id=plan_id, history=history)
        if not self.key or not eligible or not original.to_dict()["unresolved"]:
            return original
        text = events[0].payload["text"]
        target = density_target(text)
        if target is None or not any(layer.name == target and layer.active and layer.density > 0 for layer in state.layers):
            self.last_trace["reason"] = "outside_density_gate"
            return original
        reply = ask_jev(text, state, self.key)
        self.last_trace["jev"] = reply
        if "error" in reply:
            self.last_trace["reason"] = "provider_error"
            return original
        selections = reply["selected"]
        if selections != {"effect": "simplify", "target": target}:
            self.last_trace["reason"] = "candidate_not_scoped_density"
            return original
        if not all(passes_confidence(reply["answers"][field]) for field in selections):
            self.last_trace["reason"] = "below_confidence_gate"
            return original
        try:
            plan = density_plan(events, state, plan_id, target)
        except PlanError:
            self.last_trace["reason"] = "plan_rejected"
            return original
        self.last_trace["source"] = "jev_density"
        return plan


def scenario(case):
    events = [{"kind": "text", "id": "feedback", "text": case["text"]}]
    stop = case.get("system_stop", False)
    if stop:
        events.insert(0, {"kind": "stop", "id": "stop"})
    bpm = case.get("bpm", 96)
    tempo, simplify, mute = (case.get(field) for field in ("tempo", "simplify", "mute"))
    apply = stop or tempo is not None or simplify or mute
    bar = case.get("bar", 1)
    expected = {"status": "applied" if apply else "held", "bar": bar, "accepted_ids": [e["id"] for e in events]}
    if apply:
        expected["tempo"] = tempo if tempo is not None else bpm
    compared = {layer: "same" for layer in LAYERS if layer != mute}
    if simplify:
        compared[simplify] = "fewer_onsets"
    target_notes = {mute: 0} if mute else {}
    if stop:
        compared = {}
        target_notes = dict.fromkeys(LAYERS, 0)
    return {
        "id": case["id"], "family": case["family"], "split": case["split"], "bpm": bpm,
        "finish_ms": math.ceil((bar + 5) * 240000 / bpm),
        "turns": [{"id": "turn", "at_ms": case.get("arrival_ms", 450),
                   "decision_ms": case.get("decision_ms", 0), "events": events, "expect": expected}],
        "final": {"bpm": tempo if tempo is not None else bpm, "playing": not stop,
                  "muted": [mute] if mute else [], "rhythm": None, "motif": None},
        "bar_checks": [{"bar": bar, "baseline_bars": case.get("bars", 4),
                        "baseline": compared, "notes": target_notes}],
    }


def check_case(case, spec, name, key):
    agent = RuleAgent() if name == "rule" else ExperimentalAgent(key if name == "jev" else None)
    row = Replay(spec, {}, agent).run()
    (turn,) = row["turns"]
    plan = turn.get("plan", {})
    actions = {(action["action"], action["scope"]) for action in plan.get("actions", [])}
    expected = set()
    if layer := case.get("simplify"):
        expected.add(("SIMPLIFY", layer))
    if layer := case.get("mute"):
        expected.add(("REMOVE_LAYER", layer))
    row["checks"]["exact_actions"] = actions == expected
    if "bars" in case:
        row["checks"]["declared_bars"] = plan.get("valid_for_bars") == case["bars"]
    if not case.get("system_stop") and not expected and "tempo" not in case:
        row["checks"]["no_mutation"] = (
            not actions and turn["status"] == "held" and not any(bar["receipt"] for bar in row["bars"].values())
        )
    return row


def report(rows):
    traces = [row["turns"][0]["agent_trace"] for row in rows]
    requests = [trace["jev"] for trace in traces if "jev" in trace]
    failures = {
        row["id"]: [name for name, ok in row["checks"].items() if not ok]
        for row in rows if not all(row["checks"].values())
    }
    groups = {}
    for row in rows:
        group = groups.setdefault(row["split"], {"cases": 0, "passed": 0})
        group["cases"] += 1
        group["passed"] += row["id"] not in failures
    negative = [row for row in rows if "no_mutation" in row["checks"]]
    return {
        "cases": len(rows), "passed": len(rows) - len(failures), "by_split": groups, "failures": failures,
        "routes": Counter(trace.get("source", "rule") for trace in traces),
        "reasons": Counter(trace["reason"] for trace in traces if "reason" in trace),
        "no_mutation_cases": len(negative),
        "negative_cases_modified": [row["id"] for row in negative if row["turns"][0]["status"] == "applied"],
        "jev_requests": len(requests), "jev_errors": Counter(r["error"] for r in requests if "error" in r),
        "jev_models": Counter(r["model"] for r in requests if "model" in r),
        "jev_request_p50_ms": median(r["elapsed_ms"] for r in requests) if requests else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--jev", action="store_true", help="include the network-backed arm")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    data = FIXTURE.read_bytes()
    cases = json.loads(data)
    suite = [scenario(case) for case in cases]
    validate(suite, {})
    if args.dry_run:
        print(f"Validated {len(suite)} frozen cases.")
        return
    key = os.environ.get("OHMYGPT_API_KEY") if args.jev else None
    if args.out is None or (args.jev and not key):
        parser.error("a new --out directory is required; --jev also needs OHMYGPT_API_KEY")
    args.out.mkdir(parents=True, exist_ok=False)
    arms = ("rule", "local", "jev") if key else ("rule", "local")
    summary = {"fixture_sha256": sha256(data).hexdigest(), "min_jev_confidence": MIN_CONFIDENCE, "arms": {}}
    for name in arms:
        with ThreadPoolExecutor(max_workers=3 if name == "jev" else 1) as pool:
            rows = list(pool.map(lambda pair: check_case(*pair, name, key), zip(cases, suite)))
        summary["arms"][name] = report(rows)
        path = args.out / f"{name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    (args.out / "summary.json").write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
