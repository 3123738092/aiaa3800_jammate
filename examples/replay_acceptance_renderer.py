"""Audit saved MIDI and replay a session after an executor fix; no LLM calls."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import mido

from run_acceptance_batch import write, check_expectation
from jammate.contracts import BandPlan, load_input_event
from jammate.music import Note
from jammate.orchestration.manager import StateManager
from jammate.state import JamState
from renderer_experiments.app.band_plan import KevinRuntime, CHANNELS


def read_performance(path, plan, bpm):
    seconds, notes = 0., []
    instruments = {value: key for key, value in CHANNELS.items()}
    for message in mido.MidiFile(path):
        seconds += message.time
        if message.type == "note_on" and message.velocity:
            notes.append(Note(message.note, seconds * bpm / 60 + plan.to_dict()["effective_at"]["bar"] * 4,
                              .01, message.velocity, instruments[message.channel]))
    # Only onset counts are consumed by the audit. Durations above are placeholders,
    # never rendered or written as a transcription/plan artifact.
    return SimpleNamespace(notes=notes)


def replay(args):
    data = json.loads((args.batch / "manifest.json").read_text(encoding="utf-8"))
    cases = next((s["turns"] for s in data["text_sessions"] if s["id"] == args.session), None)
    rows = json.loads((args.source_run / "report.json").read_text(encoding="utf-8"))["agent_renderer"]
    args.out.mkdir(parents=True, exist_ok=False)
    m = StateManager(JamState(session_id=args.session, tempo_bpm=100.))
    report = {"source_run": str(args.source_run), "session": args.session, "api_calls": 0,
              "kind": "post-fix replay of saved plans; not a new model evaluation", "turns": []}
    selected = [r for r in rows if r["id"] == args.session or r["id"].startswith(args.session + "/")]
    if not selected:
        raise ValueError("unknown session")
    for index, original in enumerate(selected, 1):
        label = original["id"]
        expected = cases[index - 1]["expect"] if cases else {}
        folder = args.source_run / label
        plan = BandPlan.from_dict(json.loads((folder / "plan.json").read_text(encoding="utf-8")))
        before = JamState.from_dict(json.loads((folder / "state_observed.json").read_text(encoding="utf-8")))
        after = JamState.from_dict(json.loads((folder / "state_applied.json").read_text(encoding="utf-8")))
        actual = read_performance(args.source_run / original["artifacts"] / "band.mid", plan, after.tempo_bpm)
        original_failures = check_expectation(before, after, plan, expected, performance=actual)
        m.ingest([load_input_event(p) for p in sorted(folder.glob("input_*.json"))])
        state = m.state
        m.stage(plan)
        runtime = KevinRuntime(args.out / f"turn_{index:02d}", executable=args.fluidsynth, soundfont=args.soundfont)
        result = runtime.execute(m, plan)
        failures = check_expectation(state, m.state, plan, expected, performance=result)
        row = {"id": label, "original_artifact_failures": original_failures,
               "replay_failures": failures, "replay_passed": not failures,
               "artifacts": runtime.last_artifacts.relative_to(args.out).as_posix()}
        report["turns"].append(row)
        write(args.out / "report.json", report)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fluidsynth", required=True, type=Path)
    parser.add_argument("--soundfont", required=True, type=Path)
    replay(parser.parse_args())
