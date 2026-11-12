"""File-based contract checks/wrapping, without a microphone, model or network."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jammate.rhythm import RhythmContext

from .adapters import from_humming, from_rhythm, from_text
from .input_event import InputEventError, load_input_event, read_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jammate-events", description="Validate or create InputEvent v1 files")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate existing event JSON files")
    validate.add_argument("files", nargs="+", type=Path)
    for name in ("wrap-rhythm", "wrap-humming", "text"):
        command = sub.add_parser(name)
        command.add_argument("input", help="rhythm/melody JSON filename, or the original text")
        command.add_argument("--session-id", required=True)
        command.add_argument("--event-id", help="new event ID; defaults to a UUID, retain it when retrying")
        command.add_argument("--started-at", type=float, help="capture start in session seconds; omit when unknown")
        command.add_argument("--out", type=Path, required=True)
        if name == "wrap-rhythm":
            command.add_argument("--audio-duration", type=float, required=True)
            command.add_argument("--tempo-source", choices=("detected", "manual", "session"), required=True)
            command.add_argument("--confidence-kind", choices=("grid_alignment_heuristic", "manual", "unspecified"), default="unspecified")
        if name == "text":
            command.add_argument("--ended-at", type=float, help="use the same time as start for an instantaneous typed submission")
            command.add_argument("--source", choices=("text", "speech"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            for path in args.files:
                event = load_input_event(path)
                print(json.dumps({"file": str(path), "valid": True, "event_id": event.event_id,
                                  "session_id": event.session_id, "event_type": event.event_type}, ensure_ascii=False))
            return 0
        common = {"session_id": args.session_id, "event_id": args.event_id, "started_at": args.started_at}
        if args.command == "text":
            event = from_text(args.input, source=args.source, ended_at=args.ended_at, **common)
        elif args.command == "wrap-humming":
            event = from_humming(read_json(args.input), analysis_ref=str(args.input), **common)
        else:
            data = read_json(args.input)
            if not isinstance(data, dict):
                raise InputEventError("rhythm input must be an object")
            event = from_rhythm(RhythmContext(**data), audio_duration_s=args.audio_duration,
                                tempo_source=args.tempo_source, confidence_kind=args.confidence_kind,
                                analysis_ref=str(args.input), **common)
        event.write(args.out)
        print(json.dumps({"file": str(args.out), "valid": True, "event_id": event.event_id}, ensure_ascii=False))
        return 0
    except (InputEventError, OSError, ValueError, TypeError, OverflowError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
