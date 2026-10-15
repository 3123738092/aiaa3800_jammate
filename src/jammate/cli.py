"""Command line: `python -m jammate demo-input | offline | live`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import librosa
import soundfile as sf

from jammate.demo import DEMO_MELODY, synth_hum
from jammate.pipeline import JamResult, jam_offline
from jammate.render.midi_out import write_midi
from jammate.render.synth import SR
from jammate.session_log import SessionLog, decision_record
from jammate.state import MAX_TEMPO_BPM, MIN_TEMPO_BPM

LOG = logging.getLogger("jammate")
_MAX_BARS = 256


def _bpm(text: str) -> float:
    value = float(text)
    if not MIN_TEMPO_BPM <= value <= MAX_TEMPO_BPM:
        raise argparse.ArgumentTypeError(
            f"tempo must be within {MIN_TEMPO_BPM:.0f}-{MAX_TEMPO_BPM:.0f} BPM, got {text}")
    return value


def _bars(text: str) -> int:
    value = int(text)
    if not 1 <= value <= _MAX_BARS:
        raise argparse.ArgumentTypeError(f"bars must be within 1-{_MAX_BARS}, got {text}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jammate", description="Jam with an AI band.")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo-input", help="write a synthetic count-in + hum to WAV")
    demo.add_argument("--out", type=Path, default=Path("demo_hum.wav"))
    demo.add_argument("--tempo", type=_bpm, default=100.0)
    demo.set_defaults(func=_cmd_demo_input)

    offline = sub.add_parser("offline", help="answer a recorded phrase and write the jam")
    offline.add_argument("input", type=Path, help="WAV/FLAC/MP3: optional claps, then a hum")
    offline.add_argument("--out", type=Path, default=Path("jam.wav"))
    offline.add_argument("--midi", type=Path, help="also export the band as MIDI")
    offline.add_argument("--log", type=Path, help="append the session to this JSONL file")
    offline.add_argument("--bars", type=_bars, default=8)
    offline.add_argument("--seed", type=int, default=0)
    offline.add_argument("--tempo", type=_bpm, help="BPM to use if there is no count-in")
    offline.set_defaults(func=_cmd_offline)

    live = sub.add_parser("live", help="jam in real time with the microphone (use headphones)")
    live.add_argument("--tempo", type=_bpm, default=96.0)
    live.add_argument("--seed", type=int, default=0)
    live.add_argument("--log", type=Path)
    live.set_defaults(func=_cmd_live)
    return parser


def _cmd_demo_input(args: argparse.Namespace) -> int:
    audio = synth_hum(DEMO_MELODY, tempo_bpm=args.tempo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, audio, SR)
    LOG.info("wrote %s (%.1f s, 4 claps then an E-major hum at %.0f BPM)",
             args.out, audio.size / SR, args.tempo)
    return 0


def _cmd_offline(args: argparse.Namespace) -> int:
    if not args.input.is_file():
        LOG.error("input file not found: %s", args.input)
        return 2
    try:
        y, _ = librosa.load(args.input, sr=SR, mono=True)
    except Exception as exc:  # librosa surfaces soundfile *and* audioread decoder errors
        LOG.error("could not read audio from %s: %s", args.input, exc)
        return 2
    result = jam_offline(y, SR, bars=args.bars, seed=args.seed, tempo_hint=args.tempo)
    analysis = result.analysis
    LOG.info("heard %d notes, tempo %.1f BPM (%s)", len(analysis.notes), analysis.tempo_bpm,
             analysis.tempo_source)
    for decision in result.decisions:
        LOG.info("  %-8s %s", decision.action.value, decision.reason)
    try:
        _write_offline_outputs(args, result)
    except (OSError, RuntimeError) as exc:  # sf.LibsndfileError is a RuntimeError
        LOG.error("could not write outputs: %s", exc)
        return 2
    return 0


def _write_offline_outputs(args: argparse.Namespace, result: JamResult) -> None:
    analysis = result.analysis
    log = SessionLog(args.log)
    log.write("analysis", input=str(args.input), tempo_bpm=round(analysis.tempo_bpm, 2),
              tempo_source=analysis.tempo_source, origin_s=round(analysis.origin_s, 3),
              notes=[[n.pitch, n.start, n.duration] for n in analysis.notes])
    for decision in result.decisions:
        log.write("decision", **decision_record(decision))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, result.mix_audio, SR)
    LOG.info("wrote %s", args.out)
    if args.midi:
        LOG.info("wrote %s", write_midi(result.notes, result.decisions[0].state.tempo_bpm, args.midi))


def _cmd_live(args: argparse.Namespace) -> int:
    try:
        from jammate.live import run_live
    except (ImportError, OSError) as exc:  # sounddevice missing / PortAudio library missing
        LOG.error("live mode needs sounddevice (pip install sounddevice): %s", exc)
        return 2
    return run_live(tempo_bpm=args.tempo, seed=args.seed, log_path=args.log)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    return args.func(args)
