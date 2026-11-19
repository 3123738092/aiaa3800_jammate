"""Paired Jev and Mini checks of grounded tempo decisions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from math import isclose
import os
from pathlib import Path
from statistics import median
from time import perf_counter

from jev_probe import NUMBER, run_case
from jammate.contracts.adapters import from_text
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.llm import LLMAgent, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.orchestration.tempo_intent import compile_jev_tempo
from jammate.state import JamState


def state_summary(state):
    return {"bpm": state.tempo_bpm, "next_bar": state.position.next_bar,
            "playing": state.playing, "state_version": state.state_version,
            "layers": {layer.name: {"active": layer.active, "level": layer.level, "density": layer.density}
                       for layer in state.layers}}


def execute(manager, plan):
    if plan is None:
        return "abstain", 0
    if not should_execute(manager.state, plan):
        return "held", 0
    if not manager.stage(plan):
        raise ValueError("unexpected staging failure")
    clip = OfflineRuntime().execute(manager, plan)
    if manager.plan_status(plan.plan_id) != "applied":
        raise ValueError("expected an applied execution receipt")
    return "applied", len(clip.notes)


def jev_choice(case, key):
    if "inject_jev" in case:
        return case["inject_jev"], {"source": "injected", "elapsed_ms": 0}
    # Keep the fixture's assertion fields out of the API context.
    probe = {"text": case["text"], "state": {"tempo_bpm": case["bpm"],
                                           "playing": case.get("playing", True)}, "expected": {}}
    evidence = run_case(probe, key)
    if "error" in evidence:
        return None, evidence
    mode, _, occurrence = evidence["selected"]["tempo"].partition("_")
    numbers = [match.group() for match in NUMBER.finditer(case["text"])]
    candidate = None
    if mode in {"set", "down", "up"} and occurrence.isdigit() and 1 <= int(occurrence) <= len(numbers):
        candidate = {"mode": mode, "value": numbers[int(occurrence) - 1]}
    return candidate, evidence


def grade(case, path, result):
    status = result["status"]
    if status != case[path]:
        return False
    before, after = result["before"], result["after"]
    if status in {"held", "abstain"}:
        return before == after and result["notes"] == 0
    if not isclose(after["bpm"], case["target"]) or before["playing"] != after["playing"]:
        return False
    if before["next_bar"] >= after["next_bar"]:
        return False
    if "unresolved" in case and bool(result["plan"]["unresolved"]) != case["unresolved"]:
        return False
    expected_layers = {name: props.copy() for name, props in before["layers"].items()}
    for field in ("active", "level"):
        if f"drums_{field}" in case:
            expected_layers["drums"][field] = case[f"drums_{field}"]
    for name, properties in expected_layers.items():
        for field, expected in properties.items():
            value = after["layers"][name][field]
            if (isinstance(expected, float) and not isclose(value, expected)
                    or not isinstance(expected, float) and value != expected):
                return False
    return expected_layers.keys() == after["layers"].keys()


def perform(case, path, config, key):
    session_id = case["id"]
    manager = StateManager(JamState(session_id=session_id, tempo_bpm=case["bpm"],
                                   playing=case.get("playing", True)))
    batch = manager.ingest([from_text(case["text"], session_id=session_id, event_id="turn")])
    state, history = manager.decision_snapshot()
    row = {"before": state_summary(state)}
    plan = None
    start = perf_counter()
    try:
        if path == "jev":
            candidate, trace = jev_choice(case, key)
            row.update(candidate=candidate, trace=trace)
            if "error" in trace:
                raise ValueError(trace["error"])
            if candidate:
                plan = compile_jev_tempo(batch, state, "jev-plan", **candidate)
        else:
            agent = LLMAgent(config, fallback="error")
            try:
                plan = agent.decide(batch, state, plan_id="llm-plan", history=history)
            finally:
                row["trace"] = agent.last_trace
        row["decision_ms"] = round((perf_counter() - start) * 1000, 2)
        row["status"], row["notes"] = execute(manager, plan)
    except (ValueError, ProviderFailure) as exc:
        row.update(status="error", error=str(exc), notes=0)
    row.update(plan=None if plan is None else plan.to_dict(), after=state_summary(manager.state))
    row["passed"] = grade(case, path, row)
    return row


def summarize(rows, path):
    results = [row[path] for row in rows if path in row]
    provider_rows = [row for row in results if row.get("trace", {}).get("model")
                     or row.get("trace", {}).get("attempts")]
    provider_latency = [row.get("trace", {}).get("elapsed_ms") for row in provider_rows]
    gate = [row["decision_ms"] for row in results if row.get("trace", {}).get("source") == "request_gate"]
    return {"n": len(results), "passed": sum(row["passed"] for row in results),
            "outcomes": {status: sum(row["status"] == status for row in results)
                         for status in ("applied", "held", "abstain", "error")},
            "provider_rows": len(provider_rows),
            "provider_attempts": sum(len(row.get("trace", {}).get("attempts", [1])) for row in provider_rows),
            "provider_p50_ms": median([value for value in provider_latency if value is not None]),
            "request_gate_rows": len(gate), "request_gate_p50_ms": median(gate) if gate else None,
            "models": sorted({model for row in provider_rows
                              for model in ([row["trace"]["model"]] if "model" in row["trace"] else
                                            [attempt.get("model") for attempt in row["trace"]["attempts"]]) if model})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    fixture = Path(__file__).with_name("classifier_round2_cases.json").read_bytes()
    config = replace(LLMConfig.load(args.config, environ={}), model="gpt-5.4-mini", reasoning_effort="low")
    key = os.environ["OHMYGPT_API_KEY"]
    args.out.mkdir(parents=True, exist_ok=False)
    cases = json.loads(fixture)
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
    summary.update(fixture_sha256=sha256(fixture).hexdigest(), pairs=sum("llm" in row for row in rows),
                   failures={path: [row["case"]["id"] for row in rows if path in row and not row[path]["passed"]]
                             for path in ("jev", "llm")})
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
