from __future__ import annotations

import argparse
import sys
import asyncio
import json
import logging
import os
import struct
import time
import wave
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))



from dotenv import load_dotenv

from app.backends.lyria import LyriaRenderer
from app.midi import MidiRenderer
from app.analysis import analyze_audio
from app.models import MusicIntent
from app.presets import INSTRUMENT_PROFILES, STYLE_PRESETS, get_instrument, get_style
from app.midi.files import render_with_fluidsynth, write_events, write_midi
from app.midi.patterns import render_pattern



def write_wav(path: Path, pcm: bytes, sample_rate: int = 48000, channels: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels); out.setsampwidth(2); out.setframerate(sample_rate); out.writeframes(pcm)

def result_directories(round_name: str) -> tuple[Path, Path]:
    if not round_name.startswith("round-") or any(part in round_name for part in ("/", "\\", "..")):
        raise ValueError("--round must start with round- and cannot contain a path")
    root = ROOT / "results" / round_name
    audio = root / "audio"
    sessions = root / "sessions"
    audio.mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)
    return audio, sessions


def run_midi(args: argparse.Namespace) -> int:
    audio_dir, sessions_dir = result_directories(args.round)
    intent = get_style(args.style)
    instrument = get_instrument(args.instrument)
    events = render_pattern(intent, bars=args.bars, arrangement=args.arrangement)
    stamp = int(time.time())
    base = sessions_dir / f"midi_{args.style}_{args.instrument}_{args.arrangement}_{stamp}"
    write_midi(base.with_suffix(".mid"), events, intent.tempo.bpm, instrument.programs)
    write_events(base.with_suffix(".events.json"), events)
    renderer = MidiRenderer()
    port_status = "not_requested"
    if args.port is not None:
        try:
            renderer.connect(args.port); port_status = "connected"
            renderer.start(intent, bars=args.bars)
            time.sleep(args.duration)
            renderer.stop()
        except RuntimeError as exc:
            port_status = f"blocked: {exc}"
            renderer.stop()
    wav_path = audio_dir / f"midi_{args.style}_{args.instrument}_{args.arrangement}_{stamp}.wav"
    render_status = "not_requested"
    if not args.no_render:
        try:
            render_with_fluidsynth(base.with_suffix(".mid"), wav_path, args.fluidsynth, args.soundfont)
            render_status = "rendered"
        except (FileNotFoundError, RuntimeError) as exc:
            render_status = f"blocked: {exc}"
    analysis = analyze_audio(wav_path) if render_status == "rendered" else None
    transformation = {
        "style": args.style,
        "arrangement": args.arrangement,
        "drums": "style-specific kick/snare/hat placement; energy controls velocity",
        "bass": "style-specific root/chord-tone/octave/passing-note phrase; energy controls velocity",
        "harmony": "style-specific rhythmic voicings; chord symbols map to explicit GM pitches",
        "performance_layers": ["8-bar phrase structure", "drum ghost notes and fills", "bass approach notes and phrase variation", "voice-led comping", "clavinet call-and-response", "CC volume/pan/expression/reverb", "deterministic microtiming"] if args.arrangement == "performance" else [],
        "instrument_programs": {str(channel): program for channel, program in instrument.programs.items()},
    }
    result = {"experiment_id": base.name, "backend": "midi", "status": "passed", "style": args.style,
              "instrument_profile": {"name": instrument.name, "description": instrument.description, "programs": instrument.programs},
              "arrangement": args.arrangement, "prompt": intent.prompt, "intent": intent.to_dict(), "transformation": transformation,
              "midi_path": str(base.with_suffix(".mid")), "events_path": str(base.with_suffix(".events.json")),
              "wav_path": str(wav_path) if render_status == "rendered" else None, "audio_render": render_status,
              "objective_metrics": analysis, "port": port_status, "metrics": renderer.metrics.as_dict()}
    out = base.with_suffix(".json"); out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2)); return 0
async def run_lyria(args: argparse.Namespace) -> int:
    audio_dir, sessions_dir = result_directories(args.round)
    load_dotenv(ROOT / ".env")
    renderer = LyriaRenderer()
    result = {"backend": "lyria", "status": "blocked", "metrics": {}}
    try:
        intent = MusicIntent()
        await renderer.connect(); await renderer.start(intent)
        pcm = bytearray()
        async for chunk in renderer.receive(args.duration): pcm.extend(chunk)
        path = audio_dir / f"lyria_{int(time.time())}.wav"
        if pcm: write_wav(path, bytes(pcm))
        objective_metrics = analyze_audio(path) if pcm else None
        target_bpm = intent.tempo.bpm
        if objective_metrics:
            objective_metrics["requested_bpm"] = target_bpm
            raw_bpm = objective_metrics["estimated_bpm"]
            objective_metrics["bpm_error_raw"] = round(abs(raw_bpm - target_bpm), 3)
            objective_metrics["bpm_error_best_half_or_double"] = round(min(abs(raw_bpm * factor - target_bpm) for factor in (.5, 1, 2)), 3)
        result.update(status="passed", intent=intent.to_dict(), backend_input=renderer.backend_input(intent), objective_metrics=objective_metrics, metrics=renderer.metrics.as_dict(), audio_path=str(path) if pcm else None)
    except Exception as exc:  # noqa: BLE001
        result.update(reason=f"{type(exc).__name__}: {exc}", metrics=renderer.metrics.as_dict())
    finally:
        try: await renderer.stop()
        except Exception: pass
    out = sessions_dir / f"lyria_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2)); return 0 if result["status"] == "passed" else 2

def main() -> int:
    parser = argparse.ArgumentParser(description="JamMate realtime renderer CLI")
    parser.add_argument("--round", default="round-03-exploration", help="result round directory under results/")
    sub = parser.add_subparsers(dest="command", required=True)
    midi = sub.add_parser("midi-test"); midi.add_argument("--duration", type=float, default=8); midi.add_argument("--bars", type=int, default=2); midi.add_argument("--port"); midi.add_argument("--style", choices=sorted(STYLE_PRESETS), default="warm_funk"); midi.add_argument("--arrangement", choices=("basic", "performance"), default="basic"); midi.add_argument("--instrument", choices=sorted(INSTRUMENT_PROFILES), default="electric"); midi.add_argument("--fluidsynth", type=Path, default=ROOT / "tools/fluidsynth/fluidsynth-v2.6.1-win10-x64-cpp11/bin/fluidsynth.exe"); midi.add_argument("--soundfont", type=Path, default=ROOT / "tools/FluidR3_GM.sf2"); midi.add_argument("--no-render", action="store_true")
    lyria = sub.add_parser("lyria-test"); lyria.add_argument("--duration", type=float, default=25)
    args = parser.parse_args()
    return run_midi(args) if args.command == "midi-test" else asyncio.run(run_lyria(args))

if __name__ == "__main__": raise SystemExit(main())









