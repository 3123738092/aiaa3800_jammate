"""Evaluate frozen real model calls separately from input transcription quality.

Run after prepare_acceptance_inputs.py and transcribe_acceptance_humming.py.
WAVs are synthetic, never described as human recordings. No test oracle is sent
to the Agent. Existing model/protocol settings are retained for the whole run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from jammate.analysis.rhythm import analyze_rhythm_audio
from jammate.contracts.adapters import from_text, from_rhythm, from_humming
from jammate.contracts import BandPlan
from jammate.orchestration.offline import prepare_plan
from jammate.orchestration.llm import LLMAgent
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.session_log import SessionLog
from jammate.state import JamState
from renderer_experiments.app.band_plan import KevinRuntime


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def value(state, path):
    name, _, attr = path.partition(".")
    if name in ("drums", "bass", "keys", "lead"):
        layer = state.layer(name)
        return getattr(layer, attr) if attr else layer
    return getattr(state, name)


def check_expectation(before, after, plan, expected, *, performance=None):
    failures = []
    for path in expected.get("same", []):
        if value(before, path) != value(after, path):
            failures.append(f"changed preserved field {path}")
    for path, target in expected.get("value", {}).items():
        actual = value(after, path)
        if isinstance(target, bool):
            match = actual is target
        else:
            match = abs(actual - target) < 1e-7
        if not match:
            failures.append(f"{path}: expected {target}, got {actual}")
    for operation in ("increase", "decrease"):
        path = expected.get(operation)
        if path and not ((value(after, path) > value(before, path)) if operation == "increase" else (value(after, path) < value(before, path))):
            failures.append(f"{path} did not {operation}")
    if expected.get("unresolved") and not plan.to_dict()["unresolved"]:
        failures.append("unsupported request was not reported as unresolved")
    path = expected.get("decrease", "")
    if performance is not None and path.endswith(".density"):
        instrument = path.split(".")[0]
        baseline = prepare_plan(before, BandPlan.from_dict({**plan.to_dict(), "actions": []}))
        count = lambda ns: len({n.start for n in ns if n.instrument == instrument})
        if count(performance.notes) >= count(baseline.notes):
            failures.append(f"{instrument}: density decreased but actual MIDI onset count did not")
    return failures


def humming_metrics(case, document, protocol):
    wanted, actual = case["notes"], document["notes"]
    candidates = sorted((abs(a["startSec"] - start), i, j) for i, (pitch, start, _) in enumerate(wanted)
                        for j, a in enumerate(actual) if a["pitchMidi"] == pitch and abs(a["startSec"] - start) <= protocol["hum_pitch_onset_tolerance_s"])
    used_wanted, used_actual, errors = set(), set(), []
    for _, i, j in candidates:
        if i not in used_wanted and j not in used_actual:
            used_wanted.add(i)
            used_actual.add(j)
            errors.append(abs(wanted[i][2] - actual[j]["durationSec"]))
    precision = len(used_actual) / max(1, len(actual))
    recall = len(used_wanted) / max(1, len(wanted))
    duration_error = float(np.mean(errors)) if errors else None
    # Frame score separates pitch occupancy from note-boundary fragmentation.
    correct, union = 0, 0
    for t in np.arange(0, case["duration_s"], .01):
        w = {pitch for pitch, start, dur in wanted if start <= t < start + dur}
        a = {n["pitchMidi"] for n in actual if n["startSec"] <= t < n["startSec"] + n["durationSec"]}
        correct += len(w & a)
        union += len(w | a)
    return {"expected_notes": len(wanted), "detected_notes": len(actual), "precision": precision,
            "recall": recall, "mean_duration_error_s": duration_error, "pitch_frame_iou": correct / max(1, union),
            "passed": precision >= protocol["hum_min_precision"] and recall >= protocol["hum_min_recall"]
                and duration_error is not None and duration_error <= protocol["hum_max_mean_duration_error_s"]}


def run(args):
    batch = args.batch
    manifest_path = batch / "manifest.json"
    frozen_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if frozen_hash != (batch / "manifest.sha256").read_text().strip():
        raise ValueError("frozen manifest changed")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in data["files"].values():
        if hashlib.sha256((batch / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("frozen WAV changed")
    args.out.mkdir(parents=True, exist_ok=False)
    config = LLMConfig.load(args.config)
    report = {"manifest_sha256": frozen_hash, "llm": config.public_info(),
              "source": "16 authored text turns, 12 synthetic WAV, 1 simultaneous turn",
              "limitations": "No human recordings, physical sound-card playback or latency acceptance. Development batch; model calls are not seed-deterministic.",
              "input_quality": [], "agent_renderer": []}
    contexts, documents = {}, {}
    hum_report = json.loads((batch / "humming_inference/report.json").read_text(encoding="utf-8"))
    hum_quality = {r["id"]: r for r in hum_report["cases"]}

    def manager(sid):
        return StateManager(JamState(session_id=sid, tempo_bpm=100.), log=SessionLog(args.out / sid / "trace.jsonl"))

    def turn(m, events, label, expected=None):
        directory = args.out / label
        directory.mkdir(parents=True, exist_ok=True)
        row = {"id": label, "pipeline_passed": False, "semantic_passed": False}
        runtime = KevinRuntime(directory / "renderer", executable=args.fluidsynth, soundfont=args.soundfont)
        agent = LLMAgent(config, execution_validator=runtime.validate)
        try:
            accepted = m.ingest(events)
            state, history = m.decision_snapshot()
            write(directory / "state_observed.json", state.to_dict())
            for i, event in enumerate(accepted):
                event.write(directory / f"input_{i + 1}.json")
            plan = agent.decide(accepted, state, plan_id="plan_" + label.replace("/", "_"), history=history)
            plan.write(directory / "plan.json")
            write(directory / "agent_decision.json", agent.last_trace)
            row.update(decision_source=agent.last_trace["source"], decision_ms=agent.last_trace.get("elapsed_ms"),
                       actions=[a["action"] + "/" + a["scope"] for a in plan.actions], unresolved=plan.to_dict()["unresolved"],
                       attempts=len(agent.last_trace.get("attempts", [])))
            m.stage(plan)
            result = runtime.execute(m, plan)
            failures = check_expectation(state, m.state, plan, expected or {}, performance=result)
            if agent.last_trace["source"] != "llm":
                failures.append("decision did not come from LLM")
            for event in events:
                if event.event_type == "rhythm_seed":
                    if m.state.rhythm is None or list(m.state.rhythm.onset_beats) != event.payload["rhythm"]["onset_beats"]:
                        failures.append("detected rhythm not adopted")
                elif event.event_type == "musical_phrase":
                    motif_id = "motif_" + event.event_id
                    if not any(a["action"] == "ECHO_MOTIF" and a["parameters"]["motif_id"] == motif_id for a in plan.actions):
                        failures.append("input motif was not echoed")
            row.update(pipeline_passed=True, semantic_passed=not failures, failures=failures,
                       notes=len(result.notes), start_bar=result.start_bar, bars=result.bars,
                       artifacts=runtime.last_artifacts.relative_to(args.out).as_posix())
            write(directory / "state_applied.json", m.state.to_dict())
        except Exception as exc:
            row["error"] = type(exc).__name__ + ": " + str(exc)
        report["agent_renderer"].append(row)
        write(args.out / "report.json", report)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    for case in data["rhythm"]:
        row = {"id": case["id"], "kind": "rhythm", "passed": False}
        path = batch / data["files"][case["id"]]["path"]
        audio, sr = sf.read(path, dtype="float32")
        try:
            context = analyze_rhythm_audio(audio, sr)
            contexts[case["id"]] = (context, len(audio) / sr, path)
            row.update(detected=context.to_dict(), passed=not case.get("expect_rejected", False)
                       and abs(context.tempo_bpm / case["bpm"] - 1) <= data["protocol"]["rhythm_bpm_relative_tolerance"]
                       and list(context.onset_beats) == case["beats"])
            write(args.out / "rhythm_analysis" / (case["id"] + ".json"), context.to_dict())
        except ValueError as exc:
            row.update(rejected=True, reason=str(exc), passed=case.get("expect_rejected", False))
        report["input_quality"].append(row)
    for case in data["humming"]:
        row = {"id": case["id"], "kind": "humming", "quality": hum_quality[case["id"]]["quality"]}
        if row["quality"] == "rejected":
            row.update(rejected=True, passed=case.get("expect_rejected", False))
        else:
            doc = json.loads((batch / "humming_inference" / (case["id"] + "_cleaned.json")).read_text(encoding="utf-8"))
            documents[case["id"]] = doc
            row.update(humming_metrics(case, doc, data["protocol"]))
        report["input_quality"].append(row)
    write(args.out / "report.json", report)

    for session in data["text_sessions"]:
        m = manager(session["id"])
        for i, case in enumerate(session["turns"], 1):
            event = from_text(case["text"], session_id=session["id"], event_id=f"{session['id']}_{i}")
            turn(m, [event], f"{session['id']}/turn_{i:02d}", case["expect"])

    def rhythm_event(name, sid):
        context, duration, path = contexts[name]
        return from_rhythm(context, session_id=sid, event_id=name, audio_duration_s=duration,
                           tempo_source="detected", confidence_kind="grid_alignment_heuristic", audio_ref=str(path))

    def hum_event(name, sid):
        return from_humming(documents[name], session_id=sid, event_id=name,
                            audio_ref=str(batch / data["files"][name]["path"]))

    for name in contexts:
        turn(manager(name), [rhythm_event(name, name)], name)
    for name in documents:
        turn(manager(name), [hum_event(name, name)], name)
    case = data["simultaneous"]
    if case["rhythm"] in contexts and case["humming"] in documents:
        sid = case["id"]
        turn(manager(sid), [rhythm_event(case["rhythm"], sid), hum_event(case["humming"], sid),
                           from_text(case["text"], session_id=sid, event_id="multi_text")], sid,
             {"value": {"tempo_bpm": 100.}})
    report["totals"] = {"input_cases": len(report["input_quality"]),
                        "input_passed": sum(r["passed"] for r in report["input_quality"]),
                        "agent_turns": len(report["agent_renderer"]),
                        "pipeline_passed": sum(r["pipeline_passed"] for r in report["agent_renderer"]),
                        "semantic_passed": sum(r["semantic_passed"] for r in report["agent_renderer"])}
    write(args.out / "report.json", report)
    print(json.dumps(report["totals"]))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fluidsynth", required=True, type=Path)
    parser.add_argument("--soundfont", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    run(parser.parse_args())
