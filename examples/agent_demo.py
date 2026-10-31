"""Run the versioned Agent loop with offline audio and an LLM director.

python examples/agent_demo.py
python examples/agent_demo.py --agent rule
python examples/agent_demo.py --batch examples/input_events/rhythm.json examples/input_events/humming.json
Repeat --batch for successive turns. Each explicit batch is one decision turn.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from jammate.contracts import load_input_event
from jammate.contracts.adapters import from_text, from_system
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.render.midi_out import write_midi
from jammate.session_log import SessionLog
from jammate.state import JamState


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_batches():
    def text(words, number):
        return [from_text(words, session_id="jam_demo", event_id=f"feedback_{number}")]
    return [
        [load_input_event(ROOT / f"examples/input_events/{kind}.json") for kind in ("rhythm", "humming")],
        text("这个不错，鼓再猛一点", 2),
        text("太满了，留点空间", 3),
        [from_system("idle", session_id="jam_demo", event_id="idle_4", bars=2)],
        [from_system("idle", session_id="jam_demo", event_id="idle_5", bars=2)],
        text("别停，鼓收一点，刚才那句保留。", 6),
        text("停止", 7),
        text("继续", 8),
    ]


def expressive_batches():
    """Natural wording and cross-turn references; no special parser additions."""
    def text(words, number):
        return [from_text(words, session_id="jam_demo", event_id=f"expressive_{number}")]
    return [default_batches()[0],
        text("这段旋律和速度都挺好，先别动。只让鼓更有冲击力，贝斯与和弦照旧。", 2),
        text("只删掉鼓里一部分敲击，给我留些空隙唱两句。鼓的音量别降，也别减速，其他声部不动。", 3),
        text("上一轮删鼓之前那次加强幅度有点大，把鼓的力度退回最开始的水平；当前敲击密度、贝斯与键盘都别动。", 4),
        [from_system("idle", session_id="jam_demo", event_id="expressive_idle_5", bars=2)],
        [from_system("idle", session_id="jam_demo", event_id="expressive_idle_6", bars=2)],
        text("先把贝斯拿掉，其余声部继续演奏，整首不要停。", 7),
        text("把刚才拿掉的那个声部加回来，鼓和键盘保持现在这样。", 8)]


def run(batches, output: Path, *, tempo_bpm=100., seed=3800, agent=None, runtime=None):
    batches = list(batches)
    if not batches or not batches[0]:
        raise ValueError("need at least one nonempty batch")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory is not empty; choose a new --out to preserve previous runs")
    output.mkdir(parents=True, exist_ok=True)
    log = SessionLog(output / "trace.jsonl")
    m = StateManager(JamState(session_id=batches[0][0].session_id, tempo_bpm=tempo_bpm), log=log)
    agent, runtime = agent or RuleAgent(), runtime or OfflineRuntime(seed=seed)
    external_renderer = hasattr(runtime, "last_artifacts")
    segments, report, all_notes, cursor = [], [], [], 0
    tempos = set()
    write_json(output / "state_initial.json", m.state.to_dict())
    for index, batch in enumerate(batches, 1):
        accepted = m.ingest(batch)
        if not accepted:
            report.append({"turn": index, "result": "duplicate_or_stale_input"})
            continue
        snapshot, history = m.decision_snapshot()
        plan = agent.decide(accepted, snapshot, plan_id=f"demo_plan_{index:02d}", history=history)
        decision_trace = getattr(agent, "last_trace", {"source": "rule", "plan_id": plan.plan_id})
        log.write("agent_decision", **decision_trace)
        m.stage(plan)
        turn = output / f"turn_{index:02d}"
        turn.mkdir()
        for event in accepted:
            # Never use upstream-controlled event IDs as filesystem paths.
            event.write(turn / f"input_{accepted.index(event) + 1:02d}.json")
        plan.write(turn / "plan.json")
        write_json(turn / "agent_decision.json", decision_trace)
        write_json(turn / "state_observed.json", m.state.to_dict())
        result = runtime.execute(m, plan)
        sf.write(turn / "band.wav", result.audio, result.sample_rate)
        if external_renderer:
            # Keep Kevin's verified programs, exact tick encoding and end marker.
            shutil.copyfile(runtime.last_artifacts / "band.mid", turn / "band.mid")
            # Runtime can commit successfully while a diagnostic file write fails.
            # Preserve that distinction and expose its warning in the turn report.
            write_json(turn / "receipt.json", runtime.last_receipt)
        else:
            write_midi(tuple(n.shifted(-result.start_bar * 4) for n in result.notes), result.tempo_bpm, turn / "band.mid")
        write_json(turn / "state_applied.json", m.state.to_dict())
        segments.append((cursor, result.audio))
        cursor += round(result.bars * 4 * 60 / result.tempo_bpm * result.sample_rate)
        tempos.add(result.tempo_bpm)
        all_notes.extend(result.notes)
        row = {"turn": index, "plan_id": plan.plan_id, "strategy": plan.to_dict()["strategy"],
               "actions": [a["action"] + "/" + a["scope"] for a in plan.actions],
               "state_version": m.state.state_version, "start_bar": result.start_bar,
               "bars": result.bars, "note_count": len(result.notes),
               "decision_source": decision_trace["source"],
               "decision_ms": decision_trace.get("elapsed_ms"),
               "execution_persistence_warnings": runtime.last_receipt.get("persistence_warnings", []) if external_renderer else [],
               "unresolved": plan.to_dict()["unresolved"]}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))
    # Overlap releases while keeping every segment on its exact bar boundary.
    shape = (max((start + len(wave) for start, wave in segments), default=0),)
    if segments and segments[0][1].ndim == 2:
        shape += (segments[0][1].shape[1],)
    audio = np.zeros(shape, dtype=np.float32)
    for start, wave in segments:
        audio[start:start + len(wave)] += wave
    peak = float(np.max(np.abs(audio))) if audio.size else 0
    if peak > .98:
        audio *= .98 / peak
    sf.write(output / "band.wav", audio, runtime.sample_rate)
    combined_midi = len(tempos) == 1 and not external_renderer
    if combined_midi:
        write_midi(tuple(all_notes), next(iter(tempos)), output / "band.mid")
    write_json(output / "state_final.json", m.state.to_dict())
    summary = {"mode": "offline_llm_agent" if hasattr(agent, "config") else "offline_rule_agent", "seed": seed, "initial_bpm": tempo_bpm,
               "llm": agent.config.public_info() if hasattr(agent, "config") else None,
               "sample_rate": runtime.sample_rate, "duration_s": len(audio) / runtime.sample_rate,
               "renderer": "kevin_midi_fluidsynth" if external_renderer else "numpy_reference",
               "manager_diagnostics": m.diagnostics(),
               "combined_midi": combined_midi, "turns": report,
               "limitations": "Offline inputs; no microphone or live-device acceptance. decision_source distinguishes LLM/fallback/rule/control. Receipts mean synthesis/export succeeded, not physical playback. LLM output is not seed-deterministic. Kevin exports and variable-tempo sessions use per-turn MIDI."}
    write_json(output / "report.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="new output directory; defaults to a timestamped directory for the chosen agent")
    parser.add_argument("--batch", action="append", nargs="+", type=Path, help="InputEvent files constituting one explicit turn; may repeat")
    parser.add_argument("--bpm", type=float, default=100.)
    parser.add_argument("--seed", type=int, default=3800)
    parser.add_argument("--agent", choices=("llm", "rule"), default="llm")
    parser.add_argument("--renderer", choices=("reference", "kevin"), default="reference")
    parser.add_argument("--fluidsynth", type=Path, help="FluidSynth executable; required by --renderer kevin")
    parser.add_argument("--soundfont", type=Path, help="local .sf2 file; required by --renderer kevin")
    parser.add_argument("--instrument-profile", choices=("acoustic", "electric", "synth"), default="electric")
    parser.add_argument("--lead-program", type=int, default=73, help="zero-based GM program, 0-127; default flute")
    parser.add_argument("--config", type=Path, help="private config file outside the repository")
    parser.add_argument("--model", help="override the configured model")
    parser.add_argument("--reasoning-effort", choices=("default", "none", "minimal", "low", "medium", "high", "xhigh"))
    parser.add_argument("--fallback", choices=("hold", "rule", "error"), default="hold")
    parser.add_argument("--scenario", choices=("basic", "expressive"), default="basic")
    parser.add_argument("--text", action="append", help="one language turn; repeat for a conversation")
    parser.add_argument("--session-id", default="jam_demo", help="session for --text turns")
    args = parser.parse_args()
    if args.out is None:
        args.out = ROOT / "demo_out" / ("agent_llm" if args.agent == "llm" else "agent_v1") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if args.text and args.batch:
        parser.error("use --text or --batch, not both")
    if args.text:
        batches = [[from_text(words, session_id=args.session_id)] for words in args.text]
    elif args.batch:
        batches = [[load_input_event(path) for path in batch] for batch in args.batch]
    else:
        batches = expressive_batches() if args.scenario == "expressive" else default_batches()
    runtime = OfflineRuntime(seed=args.seed)
    if args.renderer == "kevin":
        if not args.fluidsynth or not args.fluidsynth.is_file() or not args.soundfont or not args.soundfont.is_file():
            parser.error("--renderer kevin requires existing --fluidsynth and --soundfont files")
        from renderer_experiments.app.band_plan import KevinRuntime
        runtime = KevinRuntime(args.out / "renderer", executable=args.fluidsynth, soundfont=args.soundfont,
                               profile=args.instrument_profile, lead_program=args.lead_program, seed=args.seed)
    agent = RuleAgent()
    if args.agent == "llm":
        from dataclasses import replace
        from jammate.orchestration.llm import LLMAgent
        from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError
        try:
            config = LLMConfig.load(args.config)
            if args.model:
                config = replace(config, model=args.model)
            if args.reasoning_effort:
                config = replace(config, reasoning_effort=None if args.reasoning_effort == "default" else args.reasoning_effort)
            agent = LLMAgent(config, fallback=args.fallback, execution_validator=runtime.validate)
        except LLMConfigurationError as exc:
            parser.error(str(exc))
    run(batches, args.out, tempo_bpm=args.bpm, seed=args.seed, agent=agent, runtime=runtime)
    print(f"Artifacts: {args.out.resolve()}")


if __name__ == "__main__":
    main()
