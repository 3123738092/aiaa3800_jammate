"""Compare rule, LLM patch, and direct LLM bass with a fixed two-bar plan."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
import sys

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_arranger import TICKS_PER_BEAT, stage_midi, write_json
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.generate.arranger import chord_for_bar
from jammate.interaction import Motif
from jammate.music import Note
from jammate.orchestration.arrangement import LLMArranger
from jammate.orchestration.bass_generation import LLMBassArranger
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime, prepare_plan
from jammate.orchestration.plans import make_plan
from jammate.rhythm import RhythmContext
from jammate.session_log import SessionLog
from jammate.state import JamState

MODES = ("rule", "patch", "direct")
RHYTHM = RhythmContext(100, (0., 1.5, 3.), (1., .65, .85))


@dataclass(frozen=True)
class Case:
    name: str
    instruction: str
    variants: tuple[str, str]
    extra_onsets: bool = False


CASES = (
    Case("tail", "保持两小节贝斯在每小节第 0、1.5、3 拍演奏。只在第二小节第 3 拍之后再加 {value} 个短过渡音，第一小节不加音。",
         ("1", "2"), extra_onsets=True),
    Case("register", "贝斯仍然跟随每小节第 0、1.5、3 拍，音高全部使用 MIDI {value} 范围内的音符。",
         ("36–47", "48–59")),
    Case("duration", "贝斯保留每小节第 0、1.5、3 拍的起音。每个贝斯音的时值为 {value} 拍。",
         ("0.25", "0.50")),
    Case("pitch_pattern", "保持贝斯在每小节第 0、1.5、3 拍起音。每小节的三个音按顺序演奏当时和弦的 {value}。",
         ("根音、根音、根音", "根音、五度音、根音")),
)


def initial_state():
    motif = Motif("fixed-lead", "user", ("seed-lead",),
                  (Note(72, .5, .5), Note(76, 2., 1.), Note(71, 4.5, .5), Note(74, 6., 1.)))
    return JamState(session_id="bass-pilot", tempo_bpm=100., energy=2, rhythm=RHYTHM,
                    motifs=(motif,), current_motif_id=motif.motif_id)


def prepare(text, log=None):
    manager = StateManager(initial_state(), log=log)
    batch = manager.ingest([from_text(text, session_id="bass-pilot", event_id="bass-request")])
    state = manager.state
    plan = make_plan(state, batch, "bass-pilot-plan", "RESPOND", [
        {"action": "ADJUST_FEEL", "scope": "bass", "strength": .25, "parameters": {"level_delta": 0.}},
        {"action": "ECHO_MOTIF", "scope": "lead", "strength": .5, "parameters": {"motif_id": "fixed-lead"}},
    ], ["tempo", "key", "groove", "layer:drums", "layer:keys", "motif:fixed-lead"],
        {}, 2, "Authorize a bass interpretation; keep harmony, groove, and melody fixed.", [])
    return manager, state, batch, plan


def score(case, index, tracks, reference, state, transport_ok):
    notes = sorted(tracks["bass"].elements())
    baseline = sorted(reference["bass"].elements())
    starts = {note[0] for note in notes}
    anchors = {round((bar*4 + beat)*TICKS_PER_BEAT) for bar in range(2) for beat in RHYTHM.onset_beats}
    extra = starts - anchors
    unedited = all(tracks[k] == reference[k] for k in ("drums", "keys", "lead"))
    requested = False
    target = case.variants[index]
    if case.name == "tail":
        requested = (anchors <= starts and len(extra) == int(target)
                     and all(7*TICKS_PER_BEAT < onset < 8*TICKS_PER_BEAT for onset in extra)
                     and all(end-start <= .5*TICKS_PER_BEAT for start, end, _, _ in notes if start in extra))
    elif case.name == "register":
        low, high = (int(part) for part in target.split("–"))
        requested = starts == anchors and all(low <= pitch <= high for _, _, pitch, _ in notes)
    elif case.name == "duration":
        requested = starts == anchors and all(end-start == float(target)*TICKS_PER_BEAT for start, end, _, _ in notes)
    elif case.name == "pitch_pattern":
        requested = starts == anchors
        for bar in range(2):
            root = chord_for_bar(state, bar).root
            fifth = chord_for_bar(state, bar).pcs[-1]
            pitches = [pitch % 12 for start, _, pitch, _ in notes if start // (4*TICKS_PER_BEAT) == bar]
            requested &= pitches == [root, root if index == 0 else fifth, root]
    return {
        "pass": bool(notes and len(starts) == len(notes) and transport_ok and unedited and requested),
        "requested_change": requested, "transport_exact": transport_ok, "untouched_voices_exact": unedited,
        "anchor_coverage": len(starts & anchors), "anchor_total": len(anchors),
        "extra_onsets_beats": [step/TICKS_PER_BEAT for step in sorted(extra)],
        "bass_signature": notes, "new_pitch_at_onset_vs_rule": len({(n[0], n[2]) for n in notes}
                                                                 - {(n[0], n[2]) for n in baseline}),
    }


def run(case, index, folder, mode, config):
    folder.mkdir(parents=True)
    request = case.instruction.format(value=case.variants[index])
    log = SessionLog(folder / "trace.jsonl")
    manager, state, batch, plan = prepare(request, log)
    reference = prepare_plan(state, plan)
    write_json(folder / "input.json", batch[0].to_dict())
    write_json(folder / "state.json", state.to_dict())
    write_json(folder / "plan.json", plan.to_dict())
    baseline, _ = stage_midi(reference, folder / "reference.mid")
    arranger = None
    if mode == "patch":
        arranger = LLMArranger(config, log=log, fallback="error")
    elif mode == "direct":
        arranger = LLMBassArranger(config, log=log, fallback="error", allow_extra_onsets=case.extra_onsets)
    try:
        manager.stage(plan)
        rendered = OfflineRuntime(arranger=arranger).execute(manager, plan)
        tracks, transport_ok = stage_midi(rendered, folder / "band.mid")
        sf.write(folder / "band.wav", rendered.audio, rendered.sample_rate)
        result = score(case, index, tracks, baseline, state, transport_ok)
        result["state_committed"] = manager.plan_status(plan.plan_id) == "applied"
    except (PlanError, ValueError) as exc:
        result = {"pass": False, "error": str(exc), "state_committed": manager.plan_status(plan.plan_id) == "applied"}
    if arranger is not None:
        result["arrangement"] = arranger.last_trace
    write_json(folder / "result.json", result)
    return result


def other_bass_properties_match(name, a, b):
    """Secondary A/B check for changes within the generated bass voice."""
    if not all("bass_signature" in row for row in (a, b)):
        return None

    def retained(signature):
        rows = []
        for start, end, pitch, velocity in signature:
            if name == "tail" and start > 7 * TICKS_PER_BEAT:
                continue
            if name == "duration" or (name == "tail" and start == 7 * TICKS_PER_BEAT):
                end = None
            if name == "register":
                pitch %= 12
            if name == "pitch_pattern" and start % (4*TICKS_PER_BEAT) == 1.5*TICKS_PER_BEAT:
                pitch = None
            rows.append((start, end, pitch, velocity))
        return rows

    return retained(a["bass_signature"]) == retained(b["bass_signature"])


def run_pair(case, repeat, out, modes, config):
    result = {"case": case.name, "repeat": repeat, "variants": case.variants, "results": {}}
    for mode in modes:
        group = out / f"{repeat:02d}" / case.name / mode
        rows = [run(case, i, group / variant, mode, config) for i, variant in enumerate(("A", "B"))]
        result["results"][mode] = {"A": rows[0], "B": rows[1],
                                   "pair_pass": all(row["pass"] for row in rows),
                                   "other_bass_properties_match": other_bass_properties_match(case.name, *rows)}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=(*MODES, "all"), default="all")
    p.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    p.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    p.add_argument("--config", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    modes = MODES if args.mode == "all" else (args.mode,)
    config = LLMConfig.load(args.config) if any(mode != "rule" for mode in modes) else None
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"model": config.model if config else None, "tempo_bpm": 100, "bars": 2,
              "comparison": "Same two-bar state, BandPlan, rhythm anchors, and context per A/B; one text value varies.",
              "pairs": []}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        jobs = [pool.submit(run_pair, case, n, args.out, modes, config)
                for n in range(1, args.repeat+1) for case in CASES]
        for job in jobs:
            report["pairs"].append(job.result())
            write_json(args.out / "report.json", report)
    summary = {}
    for mode in modes:
        rows = [pair["results"][mode] for pair in report["pairs"]]
        variants = [row[side] for row in rows for side in ("A", "B")]
        traces = [row["arrangement"] for row in variants if "arrangement" in row]
        summary[mode] = {"pairs_total": len(rows), "pairs_pass": sum(row["pair_pass"] for row in rows),
                         "passing_pairs_isolated_within_bass": sum(
                             row["pair_pass"] and row["other_bass_properties_match"] is True for row in rows),
                         "variants_total": len(variants), "variants_pass": sum(row["pass"] for row in variants),
                         "rendered": sum("bass_signature" in row for row in variants),
                         "other_voices_preserved": sum(row.get("untouched_voices_exact", False) for row in variants),
                         "calls": sum(len(t["attempts"]) for t in traces),
                         "median_arrangement_ms": median(t["elapsed_ms"] for t in traces) if traces else None}
    report["summary"] = summary
    write_json(args.out / "report.json", report)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
