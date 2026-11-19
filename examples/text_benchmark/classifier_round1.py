"""Replay frozen JamMate decisions through the shared execution gate."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from jammate.contracts.band_plan import BandPlan
from jammate.contracts.adapters import from_text
from jammate.interaction import Layer, TransportPosition
from jammate.music import Key
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.orchestration.plans import make_plan
from jammate.state import JamState


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("classifier_round1_cases.json")
TRACES = ROOT / "examples/benchmark_evidence"


def read_records(cases):
    records = {}
    hashes = {}
    for source in sorted({case["source"] for case in cases if "source" in case}):
        directory = "text_benchmark_20261118" if source == "mini_baseline" else "jev_text_shadow_20261119"
        path = TRACES / directory / source / "turns.jsonl"
        data = path.read_bytes()
        hashes[path.relative_to(ROOT).as_posix()] = sha256(data).hexdigest()
        for line in data.splitlines():
            turn = json.loads(line)
            key = (source, turn["scenario"], turn["case"])
            if key in records:
                raise ValueError(f"duplicate trace record {key}")
            records[key] = turn
    return records, hashes


def replay_input(case, record):
    if record is None:
        state = JamState(
            session_id=f"round1-{case['id']}", tempo_bpm=case["initial_bpm"],
            position=TransportPosition(4, 0, 4), state_version=2,
        )
        return state, from_text(case["input"], session_id=state.session_id, event_id=case["id"])

    source = record["before"]
    plan = record["plan"]
    if source["motif"] is not None or not source["key"]["key"] or len(plan["trigger_event_ids"]) != 1:
        raise ValueError(f"case {case['id']} needs more information than the saved compact snapshot")
    bar = source["next_bar"]
    state = JamState(
        session_id=plan["session_id"], state_version=source["state_version"], tempo_bpm=source["tempo"],
        key=Key(**source["key"]["key"]), progression_variant=source["key"]["progression_variant"],
        playing=source["playing"], layers=tuple(Layer(**layer) for layer in source["layers"].values()),
        position=TransportPosition(bar, 0, bar),
    )
    return state, from_text(record["input"], session_id=state.session_id, event_id=plan["trigger_event_ids"][0])


def select_plan(case, record, state, batch):
    if record is not None:
        return BandPlan.from_dict(record["plan"])
    transport = {}
    if "tempo_delta" in case:
        transport["tempo_bpm"] = state.tempo_bpm + case["tempo_delta"]
    return make_plan(state, batch, f"plan-{case['id']}", "FOLLOW", case.get("actions", []),
                     ["key", "groove"], transport, 4, "Frozen synthetic candidate", case.get("unresolved", []))


def effects(state):
    return {
        "playing": state.playing, "tempo": state.tempo_bpm, "next_bar": state.position.next_bar,
        "state_version": state.state_version, "layers": {layer.name: asdict(layer) for layer in state.layers},
        "key": state.to_dict()["harmony"],
    }


def expected_effects(case, state):
    want = case["expect"]
    expected = effects(state)
    expected["next_bar"] += want["bars"]
    expected["state_version"] += int(want["execute"])
    for field in ("tempo", "playing"):
        expected[field] = want.get(field, expected[field])
    for layer, overrides in want.get("layer_overrides", {}).items():
        expected["layers"][layer].update(overrides)
    expected["committed"] = expected["rendered"] = want["execute"]
    expected["staged"] = want["execute"]
    if "unresolved" in want:
        expected["unresolved"] = want["unresolved"]
    if want["execute"]:
        expected["audio_audible"] = expected["playing"]
    return expected


def replay(case, record, *, guarded):
    initial, event = replay_input(case, record)
    manager = StateManager(initial)
    batch = manager.ingest([event])
    before = manager.state
    plan = select_plan(case, record, before, batch)
    data = plan.to_dict()
    if guarded:
        execute = should_execute(before, plan)
    else:
        execute = bool(data["actions"] or data["transport"] or not data["unresolved"])
    rendered = None
    if execute:
        if not manager.stage(plan):
            raise RuntimeError(f"fresh plan could not be staged: {case['id']}")
        rendered = OfflineRuntime(sample_rate=8000).execute(manager, plan)
    applied = manager.plan_status(plan.plan_id) == "applied"
    actual = effects(manager.state)
    actual.update(committed=applied, rendered=rendered is not None, unresolved=bool(data["unresolved"]),
                  unit_count=len(rendered.notes) if rendered is not None else None,
                  staged=bool(manager.diagnostics()["plan_count"]),
                  audio_audible=bool(rendered.audio.any()) if rendered is not None else None)
    expected = expected_effects(case, before)
    problems = {field: {"actual": actual[field], "expected": value} for field, value in expected.items()
                if actual[field] != value}
    if record is not None and not guarded:
        source = record["after"]
        for field in ("next_bar", "playing", "tempo", "state_version", "layers", "key"):
            if source[field] != actual[field]:
                raise AssertionError(f"{case['id']} replay differs from its frozen archived {field}")
        if bool((record["receipt"] or {}).get("state_committed")) != applied:
            raise AssertionError(f"{case['id']} replay differs from its archived receipt")
    return {"execute": execute, "observed": actual, "gold": expected, "mismatches": problems,
            "passed": not problems, "guarded": guarded, "plan": data}


def run(out):
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    records, hashes = read_records(cases)
    rows = []
    for case in cases:
        record = records[(case["source"], case["scenario"], case["id"])] if "source" in case else None
        old = replay(case, record, guarded=False)
        new = replay(case, record, guarded=True)
        rows.append({"id": case["id"], "group": case["group"], "archived": record is not None,
                     "input": record["input"] if record is not None else case["input"], "baseline": old, "guarded": new})
    groups = {}
    for group in dict.fromkeys(row["group"] for row in rows):
        subset = [row for row in rows if row["group"] == group]
        groups[group] = {"n": len(subset), "baseline": sum(row["baseline"]["passed"] for row in subset),
                         "guarded": sum(row["guarded"]["passed"] for row in subset)}
    summary = {"total": len(rows), "archived": sum(row["archived"] for row in rows),
               "baseline_passed": sum(row["baseline"]["passed"] for row in rows),
               "guarded_passed": sum(row["guarded"]["passed"] for row in rows),
               "groups": groups, "residual": [row["id"] for row in rows if not row["guarded"]["passed"]],
               "fixture_sha256": sha256(FIXTURE.read_bytes()).hexdigest(), "trace_sha256": hashes}
    out.mkdir(parents=True, exist_ok=False)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "cases.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().out), ensure_ascii=False, indent=2))
