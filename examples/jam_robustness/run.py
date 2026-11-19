"""Replay timestamped JamMate turns through the realtime scheduling contract."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from heapq import heappop, heappush
from itertools import count
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_humming, from_rhythm, from_system, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_bar
from jammate.orchestration.realtime import RealtimeSession
from jammate.rhythm import RhythmContext
from jammate.state import JamState


HERE = Path(__file__).resolve().parent
MELODY_TEMPLATE = HERE.parent / "input_events/source/humming_melody_v1.json"
SAMPLE_RATE = 1000  # Virtual playback samples correspond to elapsed milliseconds.


def melody_profile(name, profile, template):
    melody = deepcopy(template)
    duration = profile["duration_ms"] / 1000
    notes = [
        {"id": f"n{i}", "startSec": start / 1000, "durationSec": length / 1000,
         "pitchMidi": pitch, "velocity01": velocity, "modelActivation": .9,
         "onsetActivation": .9, "sourceNoteIds": [f"n{i}"]}
        for i, (start, pitch, length, velocity) in enumerate(profile["notes"], 1)
    ]
    melody.update(id=name, status="ready" if notes else "no_notes", notes=notes, analysisElapsedMs=0)
    melody["source"].update(id=name, name=f"Benchmark fixture {name}", durationSec=duration)
    melody["input"]["processing"]["processedDurationSec"] = duration
    melody["results"]["raw"]["notes"] = notes
    melody["results"]["cleaned"].update(notes=notes, edits=[], forceMonophonic=False)
    return melody


def make_event(spec, session_id, arrival_ms, assets, seen):
    if "repeat" in spec:
        return seen[spec["repeat"]]
    kind, event_id = spec["kind"], spec["id"]
    common = {"session_id": session_id, "event_id": event_id}
    seconds = spec.get("occurred_ms", arrival_ms) / 1000
    if kind == "text":
        return from_text(spec["text"], started_at=seconds, ended_at=seconds, **common)
    if kind == "idle":
        return from_system("idle", occurred_at=seconds, bars=spec["bars"], **common)
    if kind == "activity":
        return from_system("user_activity", occurred_at=seconds, active=spec["active"], **common)
    if kind in {"start", "stop"}:
        return from_system(kind, occurred_at=seconds, **common)
    if kind == "rhythm":
        profile = assets["rhythms"][spec["profile"]]
        duration = profile["duration_ms"] / 1000
        rhythm = RhythmContext(profile["detected_bpm"], tuple(profile["onsets"]), tuple(profile["accents"]),
                               confidence=profile["confidence"])
        return from_rhythm(rhythm, audio_duration_s=duration, started_at=seconds - duration,
                           tempo_source="detected", confidence_kind="grid_alignment_heuristic", **common)
    if kind == "humming":
        melody = assets["melodies"][spec["profile"]]
        return from_humming(melody, started_at=seconds - melody["source"]["durationSec"], **common)
    raise ValueError(f"unknown input kind {kind}")


def compact(state):
    return {"bpm": state.tempo_bpm, "playing": state.playing, "strategy": state.interaction.strategy.value,
            "awaiting_user": state.interaction.awaiting_user, "user_active": state.interaction.user_active,
            "motif": state.current_motif_id, "motif_count": len(state.motifs),
            "muted": sorted(layer.name for layer in state.layers if not layer.active),
            "rhythm": list(state.rhythm.onset_beats) if state.rhythm is not None else None,
            "version": state.state_version}


def error_status(error):
    for marker, status in (("stale decision request", "stale"), ("decision deadline", "decision_late"),
                           ("enqueue deadline", "prepare_late"), ("needs clarification", "held")):
        if marker in str(error):
            return status
    return "error"


class Replay:
    def __init__(self, scenario, assets, agent):
        self.runtime = RealtimeSession(StateManager(JamState(session_id=scenario["id"], tempo_bpm=scenario["bpm"])),
                                       sample_rate=SAMPLE_RATE)
        self.scenario, self.assets, self.agent = scenario, assets, agent
        self.events, self.rows, self.bars = {}, {}, {}
        self.last_arrival_ms = None
        self.queue, self.sequence = [], count()
        for turn in scenario["turns"]:
            self.push(turn["at_ms"], "input", turn)
        (first,) = self.runtime.advance_to(0)
        self.observe_bar(first)
        self.next_bar_ms = first.timing.end_sample

    def push(self, at_ms, stage, turn, payload=None):
        priority = {"input": 0, "decision": 1, "prepared": 2}[stage]
        heappush(self.queue, (at_ms, priority, next(self.sequence), stage, turn, payload))

    def observe_bar(self, bar):
        self.bars[bar.timing.bar_index] = {
            "start_ms": bar.timing.start_sample, "end_ms": bar.timing.end_sample, "bpm": bar.timing.tempo_bpm,
            "receipt": asdict(bar.started) if bar.started else None, "state": compact(self.runtime.manager.state),
            "notes": [asdict(note) for note in bar.notes],
        }

    def advance(self, at_ms):
        while self.next_bar_ms <= at_ms:
            (bar,) = self.runtime.advance_to(self.next_bar_ms)
            self.observe_bar(bar)
            self.next_bar_ms = bar.timing.end_sample
        self.runtime.advance_to(at_ms)

    def input(self, at_ms, turn, row):
        events = tuple(make_event(spec, self.scenario["id"], at_ms, self.assets, self.events)
                       for spec in turn["events"])
        self.events.update((event.event_id, event) for event in events)
        row["gap_from_previous_input_ms"] = (None if self.last_arrival_ms is None else
                                             at_ms - self.last_arrival_ms)
        self.last_arrival_ms = at_ms
        row["events"] = [{"id": e.event_id, "source": e.source, "kind": e.event_type,
                          "event_start_s": e.started_at, "event_end_s": e.ended_at,
                          **({"text": e.payload["text"]} if e.event_type == "speech" else {})} for e in events]
        request = self.runtime.observe(events)
        if request is None:
            row["status"] = "ignored"
            return
        row.update(accepted_ids=[e.event_id for e in request.events], before=compact(request.state),
                   decision_deadline_ms=request.decision_deadline_sample)
        self.push(at_ms + turn.get("decision_ms", 0), "decision", turn, request)

    def decision(self, at_ms, turn, row, request):
        start = perf_counter()
        try:
            plan = self.agent.decide(request.events, request.state, history=request.history,
                                     plan_id=f"{self.scenario['id']}.{turn['id']}")
        finally:
            row["agent_wall_ms"] = round((perf_counter() - start) * 1000, 2)
            row["agent_trace"] = deepcopy(getattr(self.agent, "last_trace", {}))
        row["plan"] = plan.to_dict()
        bound = self.runtime.bind(request, plan)
        row["slot_bar"] = bound.slot.bar
        row["slot_start_ms"] = bound.slot.start_sample
        self.push(at_ms + turn.get("prepare_ms", 0), "prepared", turn, bound)

    def run(self):
        while self.queue:
            at_ms, _, _, stage, turn, payload = heappop(self.queue)
            self.advance(at_ms)
            row = self.rows.setdefault(turn["id"], {"id": turn["id"], "arrival_ms": turn["at_ms"]})
            try:
                if stage == "input":
                    self.input(at_ms, turn, row)
                elif stage == "decision":
                    self.decision(at_ms, turn, row, payload)
                elif self.runtime.schedule(payload.prepare()):
                    row["status"] = "queued"
                else:
                    row["status"] = "error"
            except (PlanError, ProviderFailure) as error:
                row.update(status=error_status(error), error=str(error))
        self.advance(self.scenario["finish_ms"])
        for row in self.rows.values():
            if row["status"] == "queued":
                plan = row["plan"]
                row["status"] = self.runtime.manager.plan_status(plan["plan_id"]) or "error"
                if row["status"] == "applied":
                    row["at_playback"] = self.bars[row["slot_bar"]]["state"]
                    row["playback_receipt"] = self.bars[row["slot_bar"]]["receipt"]
        return {"id": self.scenario["id"], "split": self.scenario["split"], "family": self.scenario["family"],
                "turns": list(self.rows.values()), "bars": self.bars,
                "final": compact(self.runtime.manager.state), "checks": self.checks()}

    def checks(self):
        checks = {}
        for turn in self.scenario["turns"]:
            row, expected = self.rows[turn["id"]], turn["expect"]
            plan = row.get("plan", {})
            observed = {"status": row["status"], "strategy": plan.get("strategy"), "bar": row.get("slot_bar"),
                        "accepted_ids": row.get("accepted_ids"), "tempo": row.get("at_playback", {}).get("bpm")}
            for key, value in expected.items():
                if key == "actions":
                    actions = {(a["action"], a["scope"]) for a in plan.get("actions", [])}
                    checks[f"{turn['id']}.{key}"] = all(tuple(action) in actions for action in value)
                elif key == "status" and isinstance(value, list):
                    checks[f"{turn['id']}.{key}"] = observed[key] in value
                else:
                    checks[f"{turn['id']}.{key}"] = observed[key] == value
        final = compact(self.runtime.manager.state)
        for key, value in self.scenario.get("final", {}).items():
            checks[f"final.{key}"] = final[key] == value
        for rule in self.scenario.get("bar_checks", []):
            expected = rule.get("notes", {})
            bar = self.bars[rule["bar"]]
            actual = Counter(n["instrument"] for n in bar["notes"])
            for layer, minimum in expected.items():
                checks[f"bar{rule['bar']}.{layer}"] = actual[layer] >= minimum if minimum else actual[layer] == 0
            if "baseline" in rule:
                indices = range(rule["bar"], rule["bar"] + rule.get("baseline_bars", 1))
                initial = JamState(tempo_bpm=self.scenario["bpm"])
                baseline = [asdict(note) for i in indices for note in prepare_bar(initial, i)]
                observed_notes = [note for i in indices for note in self.bars[i]["notes"]]

                def compare(layer, operation):
                    old = [n for n in baseline if n["instrument"] == layer]
                    new = [n for n in observed_notes if n["instrument"] == layer]
                    if operation == "same":
                        return old == new
                    if operation == "fewer_onsets":
                        return len({n["start"] for n in new}) < len({n["start"] for n in old})
                    if operation == "stronger":
                        return bool(old and new) and (sum(n["velocity"] for n in new) / len(new)
                                                     > sum(n["velocity"] for n in old) / len(old))
                    raise ValueError(f"unknown baseline comparison {operation}")

                for layer, operation in rule["baseline"].items():
                    checks[f"bar{rule['bar']}.{layer}.{operation}"] = compare(layer, operation)
            if "echo" in rule:
                event_id = rule["echo"]
                motif = self.runtime.manager.state.motif(f"motif_{event_id}") if any(
                    m.motif_id == f"motif_{event_id}" for m in self.runtime.manager.state.motifs) else None
                reference = sorted((n.start, n.pitch, n.duration) for n in motif.notes) if motif else []
                end = rule["bar"] + rule.get("echo_bars", 4)
                played = sorted((round(n["start"] - 4 * rule["bar"], 8), n["pitch"], n["duration"])
                                for index, output in self.bars.items() if rule["bar"] <= index < end
                                for n in output["notes"] if n["instrument"] == "lead")
                checks[f"bar{rule['bar']}.echo"] = bool(reference) and played == [
                    (round(start, 8), pitch, duration) for start, pitch, duration in reference]
        bars = list(self.bars.values())
        checks["continuous_clock"] = all(a["end_ms"] == b["start_ms"] for a, b in zip(bars, bars[1:]))
        receipts = [bar["receipt"]["plan_id"] for bar in bars if bar["receipt"]]
        checks["only_applied_plans_have_one_receipt"] = Counter(receipts) == Counter(
            row["plan"]["plan_id"] for row in self.rows.values() if row["status"] == "applied")
        return checks


def validate(suite, assets):
    case_ids = set()
    families = {}
    for scenario in suite:
        if scenario["id"] in case_ids:
            raise ValueError("duplicate scenario ID")
        case_ids.add(scenario["id"])
        if families.setdefault(scenario["family"], scenario["split"]) != scenario["split"]:
            raise ValueError("related scenarios must stay in the same split")
        if scenario["split"] not in {"dev", "holdout"}:
            raise ValueError("unknown split")
        known_events, turns = {}, set()
        previous = -1
        for turn in scenario["turns"]:
            at_ms = turn["at_ms"]
            if turn["id"] in turns or type(at_ms) is not int or at_ms < previous or at_ms < 0:
                raise ValueError(f"{scenario['id']}: invalid ID or input time")
            turns.add(turn["id"])
            previous = at_ms
            delays = (turn.get("decision_ms", 0), turn.get("prepare_ms", 0))
            if any(type(delay) is not int or delay < 0 for delay in delays) or at_ms + sum(delays) > scenario["finish_ms"]:
                raise ValueError(f"{scenario['id']}: result falls outside the observation window")
            for spec in turn["events"]:
                event = make_event(spec, scenario["id"], at_ms, assets, known_events)
                if "repeat" not in spec and event.event_id in known_events:
                    raise ValueError("use repeat for an exact duplicate event")
                known_events[event.event_id] = event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=HERE / "cases.json")
    parser.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    parser.add_argument("--case", action="append")
    parser.add_argument("--agent", choices=("rule", "llm"), default="rule")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit with status 1 if any scenario fails")
    args = parser.parse_args()
    fixture = args.fixtures.read_bytes()
    data = json.loads(fixture)
    template = MELODY_TEMPLATE.read_bytes()
    assets = {"rhythms": data["rhythms"], "melodies": {
        name: melody_profile(name, profile, json.loads(template)) for name, profile in data["melodies"].items()}}
    validate(data["scenarios"], assets)
    cases = [c for c in data["scenarios"] if (args.split == "all" or args.split == c["split"])
             and (not args.case or c["id"] in args.case)]
    if not cases or args.case and set(args.case) != {c["id"] for c in cases}:
        parser.error("no matching scenarios, or an unknown --case")
    if args.dry_run:
        print(f"Validated {len(cases)} selected scenarios")
        return
    if args.out is None:
        parser.error("--out is required outside dry-run")
    config = LLMConfig.load(args.config) if args.agent == "llm" else None
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    with (args.out / "sessions.jsonl").open("x", encoding="utf-8") as log:
        for case in cases:
            agent = LLMAgent(config, fallback="error") if config else RuleAgent()
            result = Replay(case, assets, agent).run()
            results.append(result)
            log.write(json.dumps(result, ensure_ascii=False) + "\n")
            log.flush()
    failures = {row["id"]: [name for name, passed in row["checks"].items() if not passed] for row in results}
    measurements = [turn["agent_wall_ms"] for case in results for turn in case["turns"] if "agent_wall_ms" in turn]
    summary = {"agent": args.agent, "virtual_sample_rate": SAMPLE_RATE, "fixture_sha256": sha256(fixture).hexdigest(),
               "melody_template_sha256": sha256(template).hexdigest(), "scenarios": len(results),
               "passed": sum(not values for values in failures.values()),
               "turns": sum(len(row["turns"]) for row in results),
               "statuses": dict(Counter(t["status"] for row in results for t in row["turns"])),
               "agent_wall_p50_ms": round(median(measurements), 2) if measurements else None,
               "failures": {key: value for key, value in failures.items() if value}}
    for key in ("split", "family"):
        groups = {}
        for row in results:
            group = groups.setdefault(row[key], {"scenarios": 0, "passed": 0})
            group["scenarios"] += 1
            group["passed"] += not failures[row["id"]]
        summary[f"by_{key}"] = groups
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.strict and summary["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
