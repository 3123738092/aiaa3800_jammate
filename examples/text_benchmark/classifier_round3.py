"""Paired Jev and Mini execution under the shared explicit-request checks."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from math import isclose
import os
from pathlib import Path
from statistics import median
from time import perf_counter

from classifier_round2 import state_summary, summarize
from jev_probe import NUMBER, choice, request_body, run_case
from jammate.contracts.adapters import from_text
from jammate.interaction import Layer
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.llm import LLMAgent, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.state import JamState


LAYERS = {
    "drums": "the drums/percussion (鼓)",
    "bass": "the bass (贝斯 or 低音)",
    "keys": "the keys/keyboard (键盘)",
    "lead": "the lead melody (主旋律)",
    "guitar": "the guitar (吉他)",
}
ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "examples/benchmark_evidence/classifier_iteration_20261119/round2/cases.jsonl"


def create_state(case):
    state = JamState(session_id=case["id"], tempo_bpm=case["bpm"])
    layers = state.layers + ((Layer("guitar"),) if case.get("guitar") else ())
    return replace(state, layers=tuple(replace(layer, active=False) if layer.name in case.get("inactive", [])
                                       else layer for layer in layers))


def jev_input(case, state):
    public = {"text": case["text"], "state": state_summary(state), "expected": {}}
    body = request_body(public)
    # Model sees separate slot questions. Programmatic validation owns whether the
    # combination may execute, including full-utterance coverage and conflicts.
    questions = {"tempo": body["questions"]["tempo"]}
    questions["bars"] = choice(
        "How many bars does the current user explicitly request? "
        "Return the mentioned number even if this length is out of range.",
        {"none": "No explicit requested number of bars.", "unclear": "Amount is conflicting or ambiguous.",
         **{f"bars_{i}": f"Exactly {i} bars or measures." for i in range(1, 21)}})
    for layer, name in LAYERS.items():
        questions[layer] = choice(
            f"Does the current input explicitly ask to mute or enable {name}? "
            "A request to preserve the current layer is neither operation. "
            "Detect a directly stated request even if this layer is already in that state.",
            {"none": "No explicit request to mute or enable the named layer.",
             "mute": f"Explicitly mute or stop {name}.", "enable": f"Explicitly enable or resume {name}.",
             "unclear": "The named layer is mentioned but the action contradicts itself."})
    body["questions"] = questions
    return public, body


def jev_slots(case, state, key):
    if "inject_jev" in case:
        return case["inject_jev"], {"source": "injected", "elapsed_ms": 0}
    public, body = jev_input(case, state)
    evidence = run_case(public, key, body=body)
    if "error" in evidence:
        return None, evidence
    answers = evidence["selected"]
    kind, _, ordinal = answers["tempo"].partition("_")
    values = [match.group() for match in NUMBER.finditer(case["text"])]
    tempo = None
    if kind == "keep":
        tempo = ("keep", None)
    elif kind in {"set", "up", "down"} and ordinal.isdigit() and 1 <= int(ordinal) <= len(values):
        tempo = (kind, values[int(ordinal) - 1])
    bars = int(answers["bars"].split("_")[-1]) if answers["bars"].startswith("bars_") else None
    layers = {layer: answers[layer] == "enable" for layer in LAYERS if answers[layer] in {"enable", "mute"}}
    return {"tempo": tempo, "layer_changes": layers, "bars": bars}, evidence


def execute(manager, plan):
    if plan is None:
        return "abstain", {}
    if not should_execute(manager.state, plan):
        return "held", {}
    if not manager.stage(plan):
        raise ValueError("unexpected staging failure")
    clip = OfflineRuntime().execute(manager, plan)
    if manager.plan_status(plan.plan_id) != "applied":
        raise ValueError("expected an applied execution receipt")
    return "applied", dict(Counter(note.instrument for note in clip.notes))


def grade(case, path, row):
    status = row["status"]
    if status != case[path]:
        return False
    before, after = row["before"], row["after"]
    if status in {"held", "abstain"}:
        return before == after and not row["instrument_notes"]
    if not isclose(after["bpm"], case["target"]) or before["playing"] != after["playing"]:
        return False
    if after["next_bar"] - before["next_bar"] != case["bars"]:
        return False
    if after["state_version"] != before["state_version"] + 1:
        return False
    if bool(row["plan"]["unresolved"]) != case.get("unresolved", False):
        return False
    expected_layers = {name: value.copy() for name, value in before["layers"].items()}
    for layer, active in case.get("layer_changes", {}).items():
        expected_layers[layer]["active"] = active
    return (expected_layers == after["layers"] and all(
        props["active"] or not row["instrument_notes"].get(name) for name, props in expected_layers.items()))


def perform(case, path, config, key):
    from jammate.orchestration.explicit_request import compile_jev_request

    manager = StateManager(create_state(case))
    events = manager.ingest([from_text(case["text"], session_id=case["id"], event_id="turn")])
    state, history = manager.decision_snapshot()
    row = {"before": state_summary(state)}
    plan = None
    start = perf_counter()
    try:
        if path == "jev":
            slots, trace = jev_slots(case, state, key)
            row.update(slots=slots, trace=trace)
            if "error" in trace:
                raise ValueError(trace["error"])
            # Injections share the same input shape as real classifier outputs.
            if slots.get("tempo") is not None:
                slots["tempo"] = tuple(slots["tempo"])
            plan = compile_jev_request(events, state, "jev-plan", **slots)
        else:
            agent = LLMAgent(config, fallback="error")
            try:
                plan = agent.decide(events, state, plan_id="llm-plan", history=history)
            finally:
                row["trace"] = agent.last_trace
        row["decision_ms"] = round((perf_counter() - start) * 1000, 2)
        row["status"], row["instrument_notes"] = execute(manager, plan)
    except (ValueError, ProviderFailure) as exc:
        row.update(status="error", error=str(exc), instrument_notes={})
    row.update(total_ms=round((perf_counter() - start) * 1000, 2),
               plan=None if plan is None else plan.to_dict(), after=state_summary(manager.state))
    row["passed"] = grade(case, path, row)
    return row


def archived_failure():
    for line in ARCHIVE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["case"]["id"] == "mixed_mute":
            row = record["llm"]
            return {
                "source": str(ARCHIVE.relative_to(ROOT)), "text": record["case"]["text"],
                "status": row["status"], "passed": row["passed"], "before": row["before"],
                "after": row["after"], "attempts": row["trace"]["attempts"],
            }
    raise ValueError("round-2 mixed_mute case unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    fixture = Path(__file__).with_name("classifier_round3_cases.json").read_bytes()
    cases = json.loads(fixture)
    digest = sha256(fixture).hexdigest()
    archived = archived_failure()
    assert cases[0]["text"] == archived["text"] and not archived["passed"]
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        jev_input(case, create_state(case))
    if args.dry_run:
        print(json.dumps({"cases": len(cases), "fixture_sha256": digest, "archive": archived["source"]}))
        return
    if args.out is None or args.config is None:
        parser.error("--out and --config are required for live execution")
    config = replace(LLMConfig.load(args.config, environ={}), model="gpt-5.4-mini", reasoning_effort="low")
    key = os.environ["OHMYGPT_API_KEY"]
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    with (args.out / "cases.jsonl").open("x", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        tasks = [(case, {path: pool.submit(perform, case, path, config, key)
                         for path in ("jev", "llm") if path in case}) for case in cases]
        for case, futures in tasks:
            row = {"case": case, **{path: future.result() for path, future in futures.items()}}
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
    summary = {path: summarize(rows, path) for path in ("jev", "llm")}
    for path in summary:
        summary[path]["applied_total_p50_ms"] = median([
            row[path]["total_ms"] for row in rows if path in row and row[path]["status"] == "applied"
        ]) if summary[path]["outcomes"]["applied"] else None
    summary.update(fixture_sha256=digest, pairs=sum("llm" in row for row in rows),
                   archived_mixed_mute=archived,
                   failures={path: [row["case"]["id"] for row in rows if path in row and not row[path]["passed"]]
                             for path in ("jev", "llm")})
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
