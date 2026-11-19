"""Replay the existing text benchmark through Jev beside the recorded Agent."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median, quantiles

from jev_probe import NUMBER, ROUTES, choice, request_body, run_case


HERE = Path(__file__).resolve().parent
READ_VIEW = 6
MODES = ("text_only", "context")
AGENT_SCENARIOS = {"motif_and_partial", "select_nonrecent_motif", "imagery_with_objective_direction"}
# Policy labels for this experiment, independent of the recorded model's reply.
AGENT_TURNS = {
    "add_guitar", "mute_reference", "restore_reference", "restore_original_volume",
    "mirror_previous_delta", "restore_project_start", "level_reference_b", "restore_only_keys_level_a",
    "restore_only_drums_to_initial", "mixed_reference_restore", "english_undo_last_change",
    "mute_guitar_add_lead", "clarification_selects_one",
}
TEMPO_GOLD = {
    "final_number_wins": "set_1", "subtract_from_current": "down_1", "mirror_previous_delta": "up_1",
    "restore_project_start": "restore", "outside_range_no_substitute": "set_1",
    "old_label_outside_read_view": "unclear", "add_guitar": "qualitative_up",
}
ACTIONS = {
    "layer_mute": "Mute a specific instrument or set of instruments. A global stop alone does not mute a layer.",
    "layer_enable": "Reactivate an existing layer, or explicitly enable an existing muted instrument.",
    "layer_add": "Introduce a new instrument part. Bringing back a muted existing layer does not count.",
    "level": "Change overall loudness, volume or intensity of a layer, including restoring its old level.",
    "density": "Change how frequently an accompaniment layer plays, regardless of whether the direction is supported.",
    "motif": "Write, echo, or vary a lead melody or motif.",
}
ACTION_MAP = {
    "BUILD": "level", "SIMPLIFY": "density", "ADD_LAYER": "layer_enable", "REMOVE_LAYER": "layer_mute",
    "INTRODUCE_LAYER": "layer_add", "INTRODUCE_MOTIF": "motif", "ECHO_MOTIF": "motif", "VARY_MOTIF": "motif",
}


def expected_fields(dataset, scenario, fixture, record):
    case_id = fixture["id"]
    outcome = fixture.get("outcome", "applied")
    if outcome in {"clarify", "unsupported"}:
        route = outcome
    elif outcome in {"partial", "observe"} or dataset == "prompt_methods" or scenario in AGENT_SCENARIOS or case_id in AGENT_TURNS:
        route = "agent"
    else:
        route = "fast"
    fields = {"route": route}
    tempo = TEMPO_GOLD.get(case_id)
    state_checks = fixture.get("state", {})
    absolute = state_checks.get("tempo")
    numeric_matches = [i for i, match in enumerate(NUMBER.finditer(fixture["text"]), 1)
                       if type(absolute) in {int, float} and float(match.group()) == absolute]
    # Exact requested tempo can be audited when the fixture supplies a target,
    # its literal is unambiguous, and the request itself names the BPM unit.
    if tempo is None and len(numeric_matches) == 1 and "bpm" in fixture["text"].lower() and outcome != "unsupported":
        tempo = f"set_{numeric_matches[0]}"
    if tempo is not None:
        fields["tempo"] = tempo
    changed_layers = fixture.get("preserve_other_layers")
    if changed_layers:
        fields["scope"] = changed_layers[0] if len(changed_layers) == 1 else "multiple"
    elif changed_layers == []:
        fields["scope"] = "none"
    if type(bars := fixture.get("plan", {}).get("valid_for_bars")) is int:
        fields["bars"] = f"bars_{bars}"
    # Positive intent assertions follow the authored benchmark, not an LLM's plan.
    effects = {ACTION_MAP[name] for name, _ in fixture.get("actions", []) if name in ACTION_MAP}
    for field, requirement in state_checks.items():
        if field.startswith("layers.") and isinstance(requirement, list) and requirement[0] in {"<", ">"}:
            prop = field.split(".")[-1]
            if prop in {"level", "density"}:
                effects.add(prop)
    for effect in effects:
        fields[f"effect_{effect}"] = "yes"
    if outcome in {"applied", "hold"} and isinstance(playing := state_checks.get("playing"), bool):
        before = record["before"]["playing"]
        if playing != before:
            fields["transport"] = "start" if playing else "stop"
        elif not playing:
            fields["transport"] = "stay_stopped"
    return fields


def committed(record):
    return bool((record.get("receipt") or {}).get("state_committed"))


def compact(state):
    return {name: state[name] for name in ("tempo", "playing", "next_bar", "motif", "layers")}


def history_item(record):
    return {"input": record["input"], "committed": committed(record),
            "before": compact(record["before"]), "after": compact(record["after"]),
            "actions": record["plan"].get("actions", []), "unresolved": record["plan"].get("unresolved", [])}


def load_cases(fixtures, traces):
    records = {}
    hashes = {}
    for path in traces:
        data = path.read_bytes()
        hashes[str(path)] = sha256(data).hexdigest()
        for line in data.splitlines():
            row = json.loads(line)
            key = (row["scenario"], row["case"])
            if key in records:
                raise ValueError(f"duplicate baseline turn {key}")
            records[key] = row
    cases = []
    for path in fixtures:
        data = path.read_bytes()
        hashes[str(path)] = sha256(data).hexdigest()
        suite = json.loads(data)
        dataset = path.stem
        for scenario in suite["scenarios"]:
            prior = []
            for index, fixture in enumerate(scenario["turns"], 1):
                key = (scenario["id"], fixture["id"])
                record = records.pop(key)
                if record["input"] != fixture["text"]:
                    raise ValueError(f"baseline text differs from fixture: {key}")
                cases.append({
                    "id": "/".join(key), "dataset": dataset, "scenario": scenario["id"], "turn": index,
                    "text": fixture["text"], "state": compact(record["before"]),
                    "history": prior[-READ_VIEW:],
                    "expected": expected_fields(dataset, scenario["id"], fixture, record),
                    "reference": {"passed": record["passed"], "committed": committed(record),
                                  "plan": record["plan"], "before": record["before"], "after": record["after"],
                                  "model": [reply["model"] for reply in record["raw_model_outputs"]],
                                  "agent_ms": record["trace"].get("elapsed_ms"),
                                  "elapsed_ms": record["elapsed_ms"]},
                })
                prior.append(history_item(record))
    if records:
        raise ValueError(f"unmatched baseline turns: {list(records)[:6]}")
    return cases, hashes


def build_request(case, mode):
    view = {**case, "history": case["history"] if mode == "context" else [],
            "state": case["state"] if mode == "context" else {}}
    request = request_body(view)
    questions = request["questions"]
    routes = dict(ROUTES)
    routes["fast"] += (" Include explicit keeps while muted and unambiguous no-change continuation. "
                       "Refer back only for a directly indexed committed tempo; inferred references go to agent.")
    routes["clarify"] = ("The user asks to clarify, requirements conflict, or there is an ambiguous target "
                         "with multiple plausible references. An unavailable numeric tempo with a request "
                         "to report the limit is unsupported.")
    routes["unsupported"] += " This includes an explicit out-of-range tempo when the user asks to report the limit."
    questions["route"] = choice("Choose the route for the entire current request. Reference context may be empty. "
                                "Assess held, explicit and unsupported clauses without dropping any.", routes)
    questions["tempo"]["criteria"].update({
        "qualitative_up": "Increase speed, with no numeric increment or target specified.",
        "qualitative_down": "Decrease speed, with no numeric decrement or target specified.",
    })
    questions["scope"]["criteria"]["none"] += " A global start or stop has no layer scope."
    questions["bars"] = choice("How many bars did the user explicitly request for this turn? "
                               "Ignore beat locations, prior bar counts and tempo numbers.",
                               {"unspecified": "No explicit bar count", **{f"bars_{i}": str(i) for i in range(1, 17)}})
    questions["transport"] = choice("Which global playback control does the user request?",
                                    {"unchanged": "No global start or stop request",
                                     "start": "Start or resume playback", "stop": "Stop or pause playback",
                                     "stay_stopped": "Remain stopped, even while setting a parameter"})
    for effect, description in ACTIONS.items():
        questions[f"effect_{effect}"] = choice(
            f"Does the user actively ask to do this in the current turn? {description} "
            "Ignore negated, historical or superseded instructions.",
            {"yes": "The user explicitly requests this effect.", "no": "There is no active request for this effect."})
    return request


def classify(case, mode, key):
    # Only these fields enter the provider call. Reference outputs and labels stay local.
    row = run_case(case, key, body=build_request(case, mode))
    row.pop("case")
    row["mode"] = mode
    if "selected" in row:
        row["candidate_effects"] = [name for name in ACTIONS if row["selected"][f"effect_{name}"] == "yes"]
    return row


def mode_summary(rows, mode):
    usable = [(row["case"], row["variants"][mode]) for row in rows if "error" not in row["variants"][mode]]
    counts = {}
    for field in ("route", "tempo", "scope", "bars", "transport", "effect"):
        comparisons = [pred["selected"][key] == value for case, pred in usable
                       for key, value in case["expected"].items()
                       if key == field or field == "effect" and key.startswith("effect_")]
        counts[field] = {"correct": sum(comparisons), "total": len(comparisons)}
    gates = {}
    for cutoff in (0.0, .80, .90, .95):
        selected = [(case, pred) for case, pred in usable if pred["selected"]["route"] == "fast"
                    and pred["answers"]["route"]["confidence"] >= cutoff]
        gates[str(cutoff)] = {"fast_predictions": len(selected),
                              "wrong_route": sum(c["expected"]["route"] != "fast" for c, _ in selected),
                              "all_scored_fields_correct": sum(p["matched"] for _, p in selected)}
    latency = [pred["elapsed_ms"] for _, pred in usable]
    return {"responses": len(usable), "exact": sum(pred["matched"] for _, pred in usable),
            "fields": counts, "gates": gates, "models": sorted({pred["model"] for _, pred in usable}),
            "p50_ms": round(median(latency), 1) if latency else None,
            "p95_ms": round(quantiles(latency, n=20, method="inclusive")[18], 1) if len(latency) > 1 else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, nargs="+",
                        default=[HERE / f"{name}.json" for name in ("cases", "extended_cases", "prompt_methods")])
    parser.add_argument("--trace", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cases, hashes = load_cases(args.fixtures, args.trace)
    for case in cases:
        request = build_request(case, "context")
        for field, expected in case["expected"].items():
            if expected not in request["questions"][field]["criteria"]:
                raise ValueError(f"{case['id']}: missing candidate for {field}={expected}")
    if args.dry_run:
        print(f"Matched {len(cases)} recorded turns, {len(MODES)} paired conditions per turn")
        return
    key = os.environ.get("OHMYGPT_API_KEY")
    if args.output is None or not key:
        parser.error("--output and OHMYGPT_API_KEY are required")
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    with (args.output / "turns.jsonl").open("x", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        paired = [(case, {mode: pool.submit(classify, case, mode, key) for mode in MODES}) for case in cases]
        for case, pending in paired:
            row = {"case": case, "variants": {mode: pending[mode].result() for mode in MODES}}
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
    report = {"turns": len(rows), "input_sha256": hashes, "cases_by_dataset": dict(Counter(c["dataset"] for c in cases)),
              "gold_routes": dict(Counter(c["expected"]["route"] for c in cases)),
              "baseline_passed": sum(c["reference"]["passed"] for c in cases),
              "modes": {mode: mode_summary(rows, mode) for mode in MODES},
              "by_dataset": {dataset: {mode: mode_summary([r for r in rows if r["case"]["dataset"] == dataset], mode)
                                       for mode in MODES} for dataset in sorted({c["dataset"] for c in cases})}}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("turns", "cases_by_dataset", "baseline_passed", "modes")}, indent=2))


if __name__ == "__main__":
    main()
