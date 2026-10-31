"""Score an expressive demo from its artifacts; no model or network call.

python examples/evaluate_agent_demo.py demo_out/agent_llm/<run>
These eight development cases are not a general natural-language benchmark.
"""
import argparse
import json
import statistics
from pathlib import Path


def evaluate(path: Path):
    def read(name):
        return json.loads((path / name).read_text(encoding="utf-8"))
    report = read("report.json")
    if len(report["turns"]) != 8:
        raise ValueError("expressive evaluation requires the eight-turn expressive scenario")
    states, before, plans, traces = [], [], [], []
    for index in range(1, 9):
        root = f"turn_{index:02d}"
        states.append(read(f"{root}/state_applied.json"))
        before.append(read(f"{root}/state_observed.json"))
        plans.append(read(f"{root}/plan.json"))
        traces.append(read(f"{root}/agent_decision.json"))
    layers = lambda s: {x["name"]: x for x in s["arrangement"]["layers"]}
    same_other_layers = lambda i, scope: all(layers(states[i])[k] == layers(before[i])[k] for k in layers(states[i]) if k != scope)
    tempo_same = lambda i: states[i]["transport"]["tempo_bpm"] == before[i]["transport"]["tempo_bpm"]
    l, b = [layers(s) for s in states], [layers(s) for s in before]
    initial = layers(read("state_initial.json"))
    checks = [
        ("seed_rhythm_and_phrase", plans[0]["strategy"] == "RESPOND" and states[0]["rhythm"] is not None
         and states[0]["motifs"]["current_id"] is not None and {"ECHO_MOTIF", "ADJUST_FEEL"} <= {a["action"] for a in plans[0]["actions"]}),
        ("stronger_drums_only", l[1]["drums"]["level"] > b[1]["drums"]["level"] and same_other_layers(1, "drums") and tempo_same(1)
         and states[1]["motifs"]["current_id"] == before[1]["motifs"]["current_id"]),
        ("fewer_drum_onsets_same_level", l[2]["drums"]["density"] < b[2]["drums"]["density"] and l[2]["drums"]["level"] == b[2]["drums"]["level"]
         and same_other_layers(2, "drums") and tempo_same(2)
         and report["turns"][2]["note_count"] / report["turns"][2]["bars"] < report["turns"][1]["note_count"] / report["turns"][1]["bars"]),
        ("restore_initial_level_from_history", abs(l[3]["drums"]["level"] - initial["drums"]["level"]) < 1e-6
         and l[3]["drums"]["density"] == b[3]["drums"]["density"] and same_other_layers(3, "drums") and tempo_same(3)),
        ("idle_offer_one_idea", plans[4]["strategy"] == "INITIATE" and any(a["action"] == "INTRODUCE_MOTIF" for a in plans[4]["actions"])
         and states[4]["interaction"]["awaiting_user"] and states[4]["interaction"]["strategy"] == "LISTEN"),
        ("second_idle_waits", plans[5]["strategy"] == "LISTEN" and not any(a["action"].endswith("MOTIF") for a in plans[5]["actions"])
         and states[5]["motifs"]["items"] == before[5]["motifs"]["items"]),
        ("remove_bass_without_stopping_band", not l[6]["bass"]["active"] and states[6]["transport"]["playing"] and same_other_layers(6, "bass")),
        ("restore_referred_layer", l[7]["bass"]["active"] and same_other_layers(7, "bass") and states[7]["transport"]["playing"]),
    ]
    cases = [{"turn": i + 1, "case": name, "semantic_pass": bool(passed), "decision_source": traces[i]["source"],
              "passed": bool(passed) and traces[i]["source"] == "llm"} for i, (name, passed) in enumerate(checks)]
    durations = [t["elapsed_ms"] for t in traces if "elapsed_ms" in t]
    result = {"scenario": "expressive", "passed": sum(c["passed"] for c in cases), "total": len(cases), "cases": cases,
              "llm_turns": sum(t["source"] == "llm" for t in traces), "fallback_turns": sum(t["source"] == "fallback" for t in traces),
              "repair_turns": sum(len(t.get("attempts", ())) > 1 for t in traces),
              "decision_ms": {"min": min(durations), "median": statistics.median(durations), "max": max(durations)},
              "all_cases_passed": all(c["passed"] for c in cases),
              "note": "One development scenario per recorded run, not a blind test or a general accuracy estimate. Audio is offline synthesis."}
    (path / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    result = evaluate(args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["all_cases_passed"] else 1)


if __name__ == "__main__":
    main()
