"""Minimal-pair tests for note-level arrangement. A fixed plan isolates this stage.

Run from the repository root after installing JamMate. Rule mode needs no
provider. Mock mode tests the benchmark and adapter with a scripted response.
LLM mode uses your configured provider and saves the actual MIDI and trace.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

import mido

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # renderer_experiments is a sibling of src

from jammate.contracts.adapters import from_rhythm, from_text
from jammate.contracts.band_plan import PlanError
from jammate.interaction import Motif, TransportPosition
from jammate.music import BEATS_PER_BAR, Note
from jammate.orchestration.llm import ModelReply, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.offline import PlannedPerformance, prepare_plan
from jammate.orchestration.plans import make_plan
from jammate.rhythm import RhythmContext
from jammate.session_log import SessionLog
from jammate.state import JamState
from renderer_experiments.app.band_plan import CHANNELS, midi_events
from renderer_experiments.app.midi.files import write_midi

LAYERS = tuple(CHANNELS)
SESSION_ID = "arrangement-benchmark"
EVENT_ID = "arrangement-benchmark-input"
RHYTHM_ID = "arrangement-benchmark-rhythm"
SEED = 3800
TICKS_PER_BEAT = 480
ADJACENT_STEP = 0.125
PROGRAMS = {CHANNELS["bass"]: 33, CHANNELS["keys"]: 4, CHANNELS["lead"]: 73}


@dataclass(frozen=True)
class ExpectedEdit:
    beat: float
    velocity_delta: int = 0
    shift_steps: int = 0
    gate_percent: int = 100
    drop: bool = False
    layer: str = "drums"


@dataclass(frozen=True)
class Variant:
    token: str
    edits: tuple[ExpectedEdit, ...] = ()


@dataclass(frozen=True)
class Pair:
    name: str
    factor: str
    template: str
    variants: tuple[Variant, Variant]
    expected_change: bool = True
    fixture_name: str = "drums"
    rhythm_accent_input: bool = False

    def instruction(self, variant: Variant) -> str:
        return self.template.format(value=variant.token) + " Leave every other existing note unchanged."

    def events(self, variant: Variant):
        text = from_text(self.instruction(variant), session_id=SESSION_ID, event_id=EVENT_ID)
        if self.rhythm_accent_input:
            rhythm = replace(RHYTHM, accents=(1., float(variant.token), .8, 1.))
            return text, from_rhythm(rhythm, session_id=SESSION_ID, audio_duration_s=2.4,
                                     tempo_source="session", event_id=RHYTHM_ID)
        return (text,)


RHYTHM = RhythmContext(100, (0., 1., 1.5, 3.), (1., .5, .8, 1.))

PAIRS = (
    Pair(
        "density", "number of removable hat groups (1 vs 2)",
        "Only at zero-based beats 3.5 and 2.0: remove precisely {value} closed hi-hat groups, taking the later hit first.",
        (Variant("1", (ExpectedEdit(3.5, drop=True),)),
         Variant("2", (ExpectedEdit(3.5, drop=True), ExpectedEdit(2.0, drop=True)))),
    ),
    Pair(
        "velocity", "velocity increment (+4 vs +8)",
        "At zero-based beat 1.5, increase the existing drum group's MIDI velocity by {value}.",
        (Variant("4", (ExpectedEdit(1.5, velocity_delta=4),)),
         Variant("8", (ExpectedEdit(1.5, velocity_delta=8),))),
    ),
    Pair(
        "timing", "shift direction (earlier vs later)",
        "Move the drum onset group at zero-based beat 1.5 exactly one eighth of a beat {value}, maintaining its duration.",
        (Variant("earlier", (ExpectedEdit(1.5, shift_steps=-1),)),
         Variant("later", (ExpectedEdit(1.5, shift_steps=1),))),
    ),
    Pair(
        "gate", "duration multiplier (80% vs 90%)",
        "Set the duration of the existing drum group at zero-based beat 1.5 to {value}% of its current duration.",
        (Variant("80", (ExpectedEdit(1.5, gate_percent=80),)),
         Variant("90", (ExpectedEdit(1.5, gate_percent=90),))),
    ),
    Pair(
        "protected_layer", "forbidden keyboard velocity increment (+4 vs +8)",
        "Increase the existing keyboard notes at zero-based beat 0 by a MIDI velocity of {value}.",
        (Variant("4"), Variant("8")), expected_change=False,
    ),
    Pair(
        "identical_prompt", "no input change",
        "{value}",
        (Variant("Keep every note unchanged."), Variant("Keep every note unchanged.")), expected_change=False,
    ),
    Pair(
        "bass_velocity", "bass MIDI velocity reduction (2 vs 4)",
        "只把片段第 1.5 拍的贝斯音符力度降低 {value} 个 MIDI 单位，落点和时值不变。",
        (Variant("2", (ExpectedEdit(1.5, velocity_delta=-2, layer="bass"),)),
         Variant("4", (ExpectedEdit(1.5, velocity_delta=-4, layer="bass"),))),
        fixture_name="bass_with_lead",
    ),
    Pair(
        "keys_chord_gate", "whole keyboard chord duration (90% vs 95%)",
        "Shorten the whole keyboard chord starting at beat 0 to {value}% of the original duration.",
        (Variant("90", (ExpectedEdit(0., gate_percent=90, layer="keys"),)),
         Variant("95", (ExpectedEdit(0., gate_percent=95, layer="keys"),))),
        fixture_name="keys_with_lead",
    ),
    Pair(
        "segment_boundary", "earlier is outside the segment, later is valid",
        "Move only the first drum group at beat 0 by 0.125 beats {value}. Preserve its duration and velocity. "
        "If that crosses the segment boundary, leave the group unchanged.",
        (Variant("earlier"), Variant("later", (ExpectedEdit(0., shift_steps=1),))),
    ),
    Pair(
        "protected_groove", "two requested shifts against a locked groove",
        "Move the drum onset at beat 1.5 exactly 0.125 beats {value}. "
        "If the groove is locked, keep all notes unchanged.",
        (Variant("earlier"), Variant("later")), expected_change=False, fixture_name="groove",
    ),
    Pair(
        "second_bar", "local velocity change on second bar of nonzero session position (3 vs 4)",
        "In this segment, leave the first bar untouched. Reduce the bass velocity at the downbeat "
        "of the second bar (segment beat 4) by {value} MIDI units.",
        (Variant("3", (ExpectedEdit(4., velocity_delta=-3, layer="bass"),)),
         Variant("4", (ExpectedEdit(4., velocity_delta=-4, layer="bass"),))),
        fixture_name="two_bars",
    ),
    Pair(
        "rhythm_anchor", "two shifts against rhythm already adopted by JamState",
        "Move the drum onset at beat 1 one eighth of a beat {value}. "
        "If timing is protected by the user rhythm, leave the notes unchanged.",
        (Variant("earlier"), Variant("later")), expected_change=False, fixture_name="rhythm",
    ),
    Pair(
        "input_accent", "only rhythm InputEvent accents[1] (0.54 vs 0.56)",
        "Read the second accent in the current rhythm InputEvent. Increase every drum velocity at "
        "segment beat 1 by round(100 * that accent) minus 50, without changing its timing.",
        (Variant("0.54", (ExpectedEdit(1., velocity_delta=4),)),
         Variant("0.56", (ExpectedEdit(1., velocity_delta=6),))),
        fixture_name="rhythm", rhythm_accent_input=True,
    ),
)


def fixture(name="drums", *, rhythm_event=False):
    scope = {"bass_with_lead": "bass", "keys_with_lead": "keys", "two_bars": "bass"}.get(name, "drums")
    two_bars = name == "two_bars"
    state = JamState(session_id=SESSION_ID, state_version=1, tempo_bpm=112 if two_bars else 100, energy=2,
                     position=TransportPosition(5, 0, 5) if two_bars else TransportPosition(),
                     rhythm=RHYTHM if name == "rhythm" else None)
    event = from_text("Fixed fixture event", session_id=SESSION_ID, event_id=EVENT_ID)
    events = [event]
    if rhythm_event:
        events.append(from_rhythm(RHYTHM, session_id=SESSION_ID, audio_duration_s=2.4,
                                  tempo_source="session", event_id=RHYTHM_ID))
    actions = [{"action": "ADJUST_FEEL", "scope": scope, "strength": 0.02, "parameters": {"level_delta": 0.01}}]
    preserve = ["tempo", "key", *(f"layer:{layer}" for layer in LAYERS if layer != scope)]
    if name.endswith("_with_lead"):
        motif = Motif("bench_lead", "user", ("lead_fixture",), (Note(72, .25, .5), Note(74, 1., .5)))
        state = state.with_updates(motifs=(motif,), current_motif_id=motif.motif_id)
        preserve.remove("layer:lead")
        preserve.append("motif:bench_lead")
        actions.append({"action": "ECHO_MOTIF", "scope": "lead", "strength": .5,
                        "parameters": {"motif_id": motif.motif_id}})
    if name == "groove":
        preserve.append("groove")
    plan = make_plan(
        state, events, "arrangement-benchmark-plan", "FOLLOW", actions, preserve, {}, 2 if two_bars else 1,
        f"Fixed benchmark plan with only {scope} open to local note edits.", [],
    )
    return state, plan


def expected_notes(baseline: PlannedPerformance, edits: tuple[ExpectedEdit, ...]) -> tuple[Note, ...]:
    """Apply the predeclared reference edits independently of the model adapter."""
    origin = baseline.start_bar * BEATS_PER_BAR
    by_group = {(edit.layer, origin + edit.beat): edit for edit in edits}
    remaining = set(by_group)
    notes = []
    for note in baseline.notes:
        group = note.instrument, note.start
        edit = by_group.get(group)
        if edit is None:
            notes.append(note)
            continue
        remaining.discard(group)
        if not edit.drop:
            notes.append(replace(
                note, start=note.start + edit.shift_steps * ADJACENT_STEP,
                velocity=note.velocity + edit.velocity_delta,
                duration=note.duration * edit.gate_percent / 100,
            ))
    if remaining:
        raise RuntimeError(f"Rule fixture no longer has the expected onset groups at {sorted(remaining)}")
    return tuple(notes)


def expected_midi(performance: PlannedPerformance, notes=None) -> dict[str, Counter]:
    """Quantize the reference in beats. Actual values come from saved MIDI bytes."""
    tracks = {layer: Counter() for layer in LAYERS}
    origin = performance.start_bar * BEATS_PER_BAR
    for note in performance.notes if notes is None else notes:
        tracks[note.instrument][(
            round((note.start-origin) * TICKS_PER_BEAT), round((note.end-origin) * TICKS_PER_BEAT),
            note.pitch, note.velocity,
        )] += 1
    return tracks


def decode_midi(path: Path, performance) -> tuple[dict[str, Counter], bool]:
    output = {layer: Counter() for layer in LAYERS}
    names = {channel: layer for layer, channel in CHANNELS.items()}
    midi = mido.MidiFile(path)
    open_notes = {}
    tempos = []
    tick = 0
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        if message.type == "set_tempo":
            tempos.append((tick, message.tempo))
        if message.type not in {"note_on", "note_off"}:
            continue
        key = message.channel, message.note
        if message.type == "note_on" and message.velocity > 0:
            if key in open_notes:
                raise ValueError("Overlapping same-pitch note-ons in MIDI")
            open_notes[key] = (tick, message.velocity)
        else:
            start, velocity = open_notes.pop(key)
            output[names[message.channel]][(start, tick, message.note, velocity)] += 1
    if open_notes:
        raise ValueError("MIDI contains unclosed notes")
    transport_ok = (
        tempos == [(0, mido.bpm2tempo(performance.tempo_bpm))] and midi.ticks_per_beat == TICKS_PER_BEAT
        and tick == performance.bars * BEATS_PER_BAR * TICKS_PER_BEAT
    )
    return output, transport_ok


def write_json(path: Path, document) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def stage_midi(performance: PlannedPerformance, output: Path) -> tuple[dict[str, Counter], bool]:
    duration = performance.bars * BEATS_PER_BAR * (60 / performance.tempo_bpm)
    write_midi(output, midi_events(performance), performance.tempo_bpm, PROGRAMS, duration_s=duration)
    return decode_midi(output, performance)


def difference(first: dict[str, Counter], second: dict[str, Counter]) -> dict[str, dict]:
    return {
        layer: {"removed": sorted((first[layer] - second[layer]).elements()),
                "added": sorted((second[layer] - first[layer]).elements())}
        for layer in LAYERS
    }


def metrics(tracks: dict[str, Counter]) -> dict[str, dict]:
    output = {}
    for layer, values in tracks.items():
        count = sum(values.values())
        output[layer] = {
            "notes": count,
            "onset_groups": len({row[0] for row in values}),
            "mean_velocity": round(sum(row[3] * copies for row, copies in values.items()) / count, 4) if count else None,
        }
    return output


class ScriptedProvider:
    """Return the requested reference edits for a plumbing check only."""

    def __init__(self, edits: tuple[ExpectedEdit, ...]):
        self.edits = edits

    def complete(self, messages, schema, timeout_s):
        groups = json.loads(messages[1]["content"])["groups"]
        group_ids = {(g["layer"], g["beat"]): g["id"] for g in groups}
        decisions = []
        for edit in self.edits:
            data = asdict(edit)
            data["group_id"] = group_ids[data.pop("layer"), data.pop("beat")]
            decisions.append(data)
        return ModelReply(json.dumps({"reason": "Scripted benchmark fixture", "edits": decisions}), "scripted-benchmark", {})


def run_variant(mode, pair, variant, state, plan, reference, baseline_midi, folder, config):
    events = pair.events(variant)
    write_json(folder / "input_event.json", events[0].to_dict())
    if len(events) == 2:
        write_json(folder / "rhythm_event.json", events[1].to_dict())
    expected = expected_midi(reference, expected_notes(reference, variant.edits))
    result = {"input_value": variant.token, "expected": metrics(expected)}
    scope = plan.actions[0]["scope"]
    arranger = None
    try:
        # Fresh rule preparation per variant. The text is deliberately not a
        # factor in this backend, while the optional arranger consumes the event.
        performance = prepare_plan(state, plan, seed=SEED)
        if mode != "rule":
            from jammate.orchestration.arrangement import LLMArranger

            provider = ScriptedProvider(variant.edits) if mode == "mock" else None
            arranger = LLMArranger(config, provider=provider, log=SessionLog(folder / "model.jsonl"), fallback="error")
            performance = arranger.arrange(state, plan, performance, events=events)
        path = folder / "output.mid"
        actual, transport_ok = stage_midi(performance, path)
        result.update({
            "transport_exact": transport_ok,
            "matches_reference": transport_ok and actual == expected,
            "protected_layers_exact": all(actual[layer] == baseline_midi[layer] for layer in LAYERS if layer != scope),
            "actual": metrics(actual), "difference_from_rule": difference(baseline_midi, actual),
            "difference_from_expected": difference(expected, actual),
            "midi_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        return result, actual, expected
    except (PlanError, ProviderFailure) as exc:
        result.update({"error": type(exc).__name__, "category": getattr(exc, "kind", "plan_validation"),
                       "matches_reference": False, "protected_layers_exact": None})
        return result, None, expected
    finally:
        if arranger is not None:
            result["arrangement"] = {
                key: arranger.last_trace.get(key) for key in ("status", "source", "attempts", "failure", "elapsed_ms")
            }
            write_json(folder / "trace.json", arranger.last_trace)


def run_pair(mode, pair, state, plan, reference, baseline_midi, folder, config):
    results = []
    for label, variant in zip(("A", "B"), pair.variants):
        case_dir = folder / mode / label
        case_dir.mkdir(parents=True)
        results.append(run_variant(mode, pair, variant, state, plan, reference, baseline_midi, case_dir, config))
    (left, left_midi, left_expected), (right, right_midi, right_expected) = results
    actual_delta = None if left_midi is None or right_midi is None else difference(left_midi, right_midi)
    expected_delta = difference(left_expected, right_expected)
    output_changed = None if actual_delta is None else left_midi != right_midi
    return {
        "expected_change": pair.expected_change, "actual_change": output_changed,
        "sensitivity_correct": output_changed == pair.expected_change,
        "midi_delta_exact": actual_delta == expected_delta,
        "both_outputs_exact": left["matches_reference"] and right["matches_reference"],
        "protected_layers_exact": left["protected_layers_exact"] is True and right["protected_layers_exact"] is True,
        "A": left, "B": right, "expected_midi_delta": expected_delta, "actual_midi_delta": actual_delta,
    }


def run_case(repeat, pair, mode, fixture_data, out, config):
    folder = out / f"{repeat:02d}" / pair.name
    entry = {"case": pair.name, "factor": pair.factor, "repeat": repeat,
             "fixture": pair.fixture_name + ("_accent" if pair.rhythm_accent_input else "")}
    for run_mode in ("rule", mode) if mode != "rule" else ("rule",):
        entry[run_mode] = run_pair(run_mode, pair, *fixture_data, folder, config)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("rule", "mock", "llm"), default="rule")
    parser.add_argument("--case", action="append", choices=[p.name for p in PAIRS],
                        help="Run only selected cases. Repeat this flag to select more than one.")
    parser.add_argument("--repeat", type=int, default=1, help="Independent repeats of every A/B comparison")
    parser.add_argument("--jobs", type=int, default=1, choices=(1, 2, 3, 4),
                        help="Parallel comparisons; A/B still run in order within each pair")
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--config", type=Path, help="LLM config path, used only in llm mode")
    parser.add_argument("--require-exact", action="store_true", help="Exit nonzero if any selected mode case misses its reference")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    config = None
    if args.mode == "mock":
        config = LLMConfig(api_key="scripted-fixture", model="scripted-benchmark")
    elif args.mode == "llm":
        config = LLMConfig.load(args.config)
    args.out.mkdir(parents=True, exist_ok=False)
    selected = [p for p in PAIRS if not args.case or p.name in args.case]
    baselines = {}
    for name, rhythm_event in dict.fromkeys((pair.fixture_name, pair.rhythm_accent_input) for pair in selected):
        folder = args.out / "fixtures" / (name + ("_accent" if rhythm_event else ""))
        folder.mkdir(parents=True)
        state, plan = fixture(name, rhythm_event=rhythm_event)
        reference = prepare_plan(state, plan, seed=SEED)
        baseline_midi, transport_ok = stage_midi(reference, folder / "rule_reference.mid")
        if not transport_ok or baseline_midi != expected_midi(reference):
            raise RuntimeError(f"Rule fixture {name} changed during MIDI export")
        write_json(folder / "state.json", state.to_dict())
        write_json(folder / "plan.json", plan.to_dict())
        baselines[name, rhythm_event] = state, plan, reference, baseline_midi
    report = {
        "mode": args.mode, "repeat": args.repeat, "jobs": args.jobs, "seed": SEED,
        "provider": config.public_info() if config else None,
        "design": "Fixed JamState, BandPlan, baseline notes and event metadata per pair. "
                  "A/B changes one text token or one rhythm accent; identical_prompt changes nothing.",
        "midi_diff_legend": "Each row contains [start_tick, end_tick, pitch, velocity] at 480 ticks per beat.",
        "cases": [],
    }
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(run_case, repeat, pair, args.mode, baselines[pair.fixture_name, pair.rhythm_accent_input],
                        args.out, config)
            for repeat in range(1, args.repeat + 1) for pair in selected
        ]
        for future in futures:
            report["cases"].append(future.result())
            write_json(args.out / "report.json", report)
    runs = [case[args.mode] for case in report["cases"]]
    report["by_case"] = {}
    for pair in selected:
        cases = [row[args.mode] for row in report["cases"] if row["case"] == pair.name]
        hashes = [tuple(row[v].get("midi_sha256") for v in ("A", "B")) for row in cases]
        report["by_case"][pair.name] = {
            "exact_pair_repeats": sum(row["both_outputs_exact"] for row in cases),
            "pair_repeats": len(cases),
            "identical_across_repeats": len(set(hashes)) == 1 and all(hashes[0]) if len(cases) > 1 else None,
        }
    variants = [row[v].get("arrangement", {}) for row in runs for v in ("A", "B")]
    latency = [v["elapsed_ms"] for v in variants if v.get("elapsed_ms") is not None]
    report["summary"] = {
        "model_calls": sum(len(v.get("attempts") or []) for v in variants),
        "accepted_variants": sum(v.get("status") == "accepted" for v in variants),
        "latency_ms": {"median": median(latency), "min": min(latency), "max": max(latency)} if latency else None,
        "pairs": len(runs), "pair_sensitivity_correct": sum(r["sensitivity_correct"] for r in runs),
        "expected_controls_invariant": sum(not r["expected_change"] and r["actual_change"] is False for r in runs),
        "expected_changes_detected": sum(r["expected_change"] and r["actual_change"] is True for r in runs),
        "expected_midi_delta_exact": sum(r["midi_delta_exact"] for r in runs),
        "both_outputs_exact": sum(r["both_outputs_exact"] for r in runs),
        "protected_layers_exact": sum(r["protected_layers_exact"] for r in runs),
        "rule_baseline_invariant": sum(case["rule"]["actual_change"] is False for case in report["cases"]),
    }
    write_json(args.out / "report.json", report)
    print(json.dumps({"mode": args.mode, "out": str(args.out), **report["summary"]}, ensure_ascii=False))
    if args.require_exact and not all(r["both_outputs_exact"] for r in runs):
        sys.exit(1)


if __name__ == "__main__":
    main()
