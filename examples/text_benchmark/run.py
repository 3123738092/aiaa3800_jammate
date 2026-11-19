"""Run the actual text page worker with a recording provider and score its results."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import replace
import io
import json
import math
import operator
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from mido import MidiFile
from renderer_experiments.app.band_plan import CHANNELS
from scripts.reference_worker import ReferenceService
from jammate.orchestration.llm import LLMAgent, OpenAIProvider, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.state import JamState

COMPARE = {"=": operator.eq, ">": operator.gt, "<": operator.lt, "!=": operator.ne}
CHANNEL_NAMES = {channel: layer for layer, channel in CHANNELS.items()}


class RecordingAgent(LLMAgent):
    def __init__(self, config, **options):
        self.outputs = []
        self.plan = None
        super().__init__(config, provider=OpenAIProvider(config), **options)
        with_reply = self.provider.complete

        def record(messages, schema, timeout):
            reply = with_reply(messages, schema, timeout)
            self.outputs.append({"text": reply.text.replace(config.api_key, "[redacted]"),
                                 "model": reply.model, "usage": reply.usage})
            return reply

        self.provider.complete = record

    def decide(self, *args, **kwargs):
        plan = super().decide(*args, **kwargs)
        self.plan = plan.to_dict()
        return plan


class BenchmarkService(ReferenceService):
    def __init__(self, config, fluidsynth, soundfont):
        super().__init__(fluidsynth, soundfont)
        self.model_config = config
        self.agent = None

    def config(self):
        return self.model_config

    def make_agent(self, config, **options):
        self.agent = RecordingAgent(config, **options)
        return self.agent


def musical_state(state):
    data = state.to_dict()
    music = {"tempo": data["transport"]["tempo_bpm"], "playing": data["transport"]["playing"],
             "key": data["harmony"], "motif": data["motifs"]["current_id"],
             "layers": {layer["name"]: layer for layer in data["arrangement"]["layers"]}}
    return {**music, "music": music, "next_bar": data["transport"]["next_bar"],
            "state_version": data["state_version"]}


def media_info(result):
    midi_bytes = base64.b64decode(result["midi"])
    wav_bytes = base64.b64decode(result["wav"])
    midi = MidiFile(file=io.BytesIO(midi_bytes))
    signatures, programs = {}, {}
    for track in midi.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "program_change":
                programs[CHANNEL_NAMES.get(msg.channel, str(msg.channel))] = msg.program
            elif msg.type == "note_on" and msg.velocity:
                name = CHANNEL_NAMES.get(msg.channel, str(msg.channel))
                beat = round(tick / midi.ticks_per_beat, 5)
                signatures.setdefault(name, []).append([beat, msg.note, msg.velocity])
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        audio = {"duration_s": round(wav.getnframes() / wav.getframerate(), 4),
                 "sample_rate": wav.getframerate()}
    names = (*CHANNELS, *sorted(set(signatures) - CHANNELS.keys()))
    notes = {name: len(signatures.get(name, [])) for name in names}
    onsets = {name: len({event[0] for event in signatures.get(name, [])}) for name in names}
    return {"notes": {**notes, "all": sum(notes.values())}, "onsets": onsets,
            "note_onsets": {name: sorted(events) for name, events in signatures.items()},
            "programs": programs, "wav": audio}


def lookup(obj, path, default=None):
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def score(case, before, after, plan, audio, previous, result, error, *, earlier=None):
    checks = []
    earlier = earlier or {}
    missing = object()

    def check(name, passed, actual=None, expected=None):
        checks.append({"check": name, "passed": bool(passed), "actual": actual, "expected": expected})

    expected_outcome = case.get("outcome", "applied")
    unresolved = plan.get("unresolved", [])
    feedback = " ".join([plan.get("reason", ""), *unresolved])
    committed = bool(result and result.get("receipt", {}).get("state_committed"))
    if expected_outcome == "observe":
        check("style probe: validated plan available for review", bool(plan), bool(plan), True)
    elif expected_outcome == "applied":
        check("result: rendered and committed", committed and audio is not None, committed, True)
        check("result: no unresolved items", not unresolved, unresolved, [])
    elif expected_outcome == "partial":
        check("result: partial execution with an explicit limitation", committed and bool(unresolved), unresolved, "nonempty, committed")
    elif expected_outcome in {"unsupported", "clarify"}:
        check("result: honest hold", bool(unresolved) and before["music"] == after["music"],
              unresolved, "nonempty, unchanged music")
    elif expected_outcome == "hold":
        check("result: explicit valid hold", bool(plan) and not error.get("provider"),
              bool(plan), True)
    if case.get("feedback_contains"):
        check("feedback: names the unmet request", any(term in feedback for term in case["feedback_contains"]),
              feedback, case["feedback_contains"])
    audio = audio or {}
    domains = {"state": after, "plan": plan, **{key: audio.get(key, {}) for key in ("notes", "onsets", "programs")}}
    for domain, values in domains.items():
        for field, expected in case.get(domain, {}).items():
            actual = lookup(values, field)
            if domain == "notes" and actual is None and expected_outcome == "hold" and after["playing"] is False and not result:
                actual = 0
            op, target = expected if isinstance(expected, list) else ("=", expected)
            if isinstance(target, str) and target.startswith(("before.", "previous.", "turn.")):
                reference, path = target.split(".", 1)
                if reference == "turn":
                    turn_id, path = path.split(".", 1)
                    record = earlier.get(turn_id, {})
                    if domain == "state":
                        reference_values = record.get("after")
                    elif domain == "plan":
                        reference_values = record.get("plan")
                    else:
                        reference_values = (record.get("midi_and_wav") or {}).get(domain)
                else:
                    reference_values = before if reference == "before" else (previous or {}).get(domain, {})
                target = lookup(reference_values, path, default=missing)
                if target is missing:
                    check(f"{domain}.{field}", False, actual, "reference value unavailable")
                    continue
            if (domain == "state" and field.endswith((".level", ".density"))
                    and op in {"=", "!="} and isinstance(actual, (int, float)) and isinstance(target, (int, float))):
                equal = math.isclose(actual, target, rel_tol=0, abs_tol=1e-9)
                passed = equal if op == "=" else not equal
            else:
                passed = COMPARE[op](actual, target) if op in {"=", "!="} or (actual is not None and target is not None) else False
            check(f"{domain}.{field}", passed, actual, [op, target])
    if "preserve_other_layers" in case:
        ignore = set(case["preserve_other_layers"])
        old, new = ({k: v for k, v in state["layers"].items() if k not in ignore} for state in (before, after))
        check("unrequested layers unchanged", old == new, new, old)
    if "actions" in case:
        actual = [[action["action"], action["scope"]] for action in plan.get("actions", [])]
        for expected in case["actions"]:
            check("plan action", expected in actual, actual, expected)
    pitched_reference = case.get("same_pitched_notes_as")
    if "same_pitched_notes_as_previous" in case:
        pitched_reference = {"layer": case["same_pitched_notes_as_previous"]}
    if pitched_reference:
        name = pitched_reference["layer"]
        source = earlier.get(pitched_reference["turn"], {}).get("midi_and_wav") if "turn" in pitched_reference else previous

        def pitched_onsets(media):
            return [event[:2] for event in (media or {}).get("note_onsets", {}).get(name, [])]

        old, new = pitched_onsets(source), pitched_onsets(audio)
        check(f"note-on beat and pitch sequence: {name}", bool(old) and old == new, new, old)
    return checks


def run(args):
    config = replace(LLMConfig.load(), **({"model": args.model} if args.model else {}), timeout_s=args.timeout)
    fixtures = json.loads(args.cases.read_text(encoding="utf-8"))
    scenarios = [scenario for scenario in fixtures["scenarios"] if not args.scenario or scenario["id"] in args.scenario]
    if not scenarios or (args.scenario and set(args.scenario) != {s["id"] for s in scenarios}):
        raise SystemExit("Unknown benchmark scenario")
    args.output.mkdir(parents=True, exist_ok=False)
    service = BenchmarkService(config, args.fluidsynth, args.soundfont)
    turns = []
    with (args.output / "turns.jsonl").open("w", encoding="utf-8") as log, patch("scripts.reference_worker.LLMAgent", service.make_agent):
        for scenario in scenarios:
            sid = uuid4().hex
            state = musical_state(JamState(session_id=sid, tempo_bpm=fixtures["bpm"]))
            previous_audio, completed = None, 0
            earlier = {}
            for case in scenario["turns"]:
                before, result, audio, error = state, None, None, {}
                artifacts = {}
                service.agent = None
                start = time.monotonic()
                try:
                    result = service.text_turn({"session_id": sid, "request_id": case["id"], "after_turn": completed,
                                                "agent": "llm", "bpm": fixtures["bpm"], "profile": "electric",
                                                "arranger": "rule", "text": case["text"]})
                    completed = result["turn"]
                    audio = media_info(result)
                    if args.save_audio:
                        folder = args.output / "audio" / scenario["id"]
                        folder.mkdir(parents=True, exist_ok=True)
                        for field, extension in (("wav", "wav"), ("midi", "mid")):
                            path = folder / f"{case['id']}.{extension}"
                            path.write_bytes(base64.b64decode(result[field]))
                            artifacts[field] = path.relative_to(args.output).as_posix()
                except ProviderFailure as exc:
                    error = {"provider": exc.kind}
                except (ValueError, OSError) as exc:
                    error = {"type": type(exc).__name__, "message": str(exc).replace(config.api_key, "[redacted]")}
                if sid in service.works:
                    state = musical_state(service.works[sid].manager.state)
                agent = service.agent
                plan, trace = ((agent.plan or {}), agent.last_trace) if agent else ({}, {})
                checks = score(case, before, state, plan, audio, previous_audio, result, error, earlier=earlier)
                entry = {"scenario": scenario["id"], "case": case["id"], "input": case["text"], "passed": all(c["passed"] for c in checks),
                         "expected_outcome": case.get("outcome", "applied"), "checks": checks, "before": before, "after": state,
                         "raw_model_outputs": agent.outputs if agent else [], "plan": plan, "trace": trace,
                         "execution_error": error, "receipt": result.get("receipt") if result else None,
                         "midi_and_wav": audio, "artifacts": artifacts, "elapsed_ms": round((time.monotonic() - start) * 1000, 1)}
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                turns.append(entry)
                earlier[case["id"]] = entry
                print(f"{scenario['id']}/{case['id']}: {'PASS' if entry['passed'] else 'FAIL'}", flush=True)
                if audio is not None:
                    previous_audio = audio
    summarize(turns, args.output, config.model, config.reasoning_effort)


def rescore(args):
    fixtures = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = {(scenario["id"], case["id"]): case
             for scenario in fixtures["scenarios"] for case in scenario["turns"]}
    previous_summary = json.loads((args.rescore / "summary.json").read_text(encoding="utf-8"))
    turns, previous_audio, earlier = [], {}, {}
    with (args.rescore / "turns.jsonl").open(encoding="utf-8") as source:
        for line in source:
            entry = json.loads(line)
            scenario = entry["scenario"]
            case = cases[scenario, entry["case"]]
            result = {"receipt": entry["receipt"]} if entry["receipt"] is not None else None
            audio = entry["midi_and_wav"]
            session_turns = earlier.setdefault(scenario, {})
            checks = score(case, entry["before"], entry["after"], entry["plan"], audio,
                           previous_audio.get(scenario), result, entry["execution_error"], earlier=session_turns)
            turns.append({**entry, "checks": checks, "passed": all(c["passed"] for c in checks)})
            session_turns[entry["case"]] = entry
            if audio is not None:
                previous_audio[scenario] = audio
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "turns.jsonl").open("w", encoding="utf-8") as log:
        for entry in turns:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    summarize(turns, args.output, previous_summary["requested_model"], previous_summary["reasoning_effort"],
              rescored_from=args.rescore.name)


def summarize(turns, output_dir, model, effort, **metadata):
    usage = Counter()
    for turn in turns:
        for output in turn["raw_model_outputs"]:
            usage.update(output["usage"])
    elapsed = [t["trace"]["elapsed_ms"] for t in turns if "elapsed_ms" in t["trace"]]
    summary = {"requested_model": model, "reasoning_effort": effort,
               "returned_models": sorted({o["model"] for t in turns for o in t["raw_model_outputs"]}),
               "passed": sum(t["passed"] for t in turns), "total": len(turns),
               "model_responses": sum(len(t["raw_model_outputs"]) for t in turns),
               "attempts": sum(len(t["trace"].get("attempts", [])) for t in turns),
               "usage": dict(usage), "latency_median_ms": statistics.median(elapsed) if elapsed else None,
               "failed_cases": [{"case": t["case"], "checks": [c["check"] for c in t["checks"] if not c["passed"]],
                                 "execution_error": t["execution_error"]} for t in turns if not t["passed"]],
               "renderer": "Kevin / FluidSynth", **metadata}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--model", help="Override only the model in the private provider configuration")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--fluidsynth", type=Path)
    parser.add_argument("--rescore", type=Path, help="Score a previous run again without calling an API")
    parser.add_argument("--save-audio", action="store_true", help="Save the returned WAV and MIDI for listening and inspection")
    parser.add_argument("--soundfont", type=Path, default=ROOT / "examples/backend_inputs/TimGM6mb.sf2")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    if args.rescore:
        rescore(args)
    else:
        if args.fluidsynth is None or not args.fluidsynth.is_file() or not args.soundfont.is_file():
            parser.error("FluidSynth executable and SoundFont must exist")
        run(args)


if __name__ == "__main__":
    main()
