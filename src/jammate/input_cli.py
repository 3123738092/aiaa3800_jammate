"""python -m jammate.input_cli rhythm recording.wav --out event.json"""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from jammate.input_event import from_melody_document, perceive_rhythm, write_event


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw inputs to InputEvent (no band generation)")
    parser.add_argument("source", choices=["rhythm", "humming-json"])
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--session-id", default=f"session_{uuid4().hex}")
    parser.add_argument("--started-at", type=float, default=0)
    parser.add_argument("--count-in", type=int, default=4)
    parser.add_argument("--tempo", type=float)
    parser.add_argument("--origin", type=float)
    args = parser.parse_args(argv)
    try:
        if args.source == "humming-json":
            event = from_melody_document(json.loads(args.input.read_text(encoding="utf-8")),
                                        session_id=args.session_id, started_at=args.started_at)
        else:
            import librosa
            y, sr = librosa.load(args.input, sr=22050, mono=True)
            event = perceive_rhythm(y, sr, session_id=args.session_id, started_at=args.started_at,
                                   count_in=args.count_in, tempo_hint=args.tempo, beat_origin_s=args.origin)
        write_event(event, args.out)
        print(f"{event['source']} -> {args.out}")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"input error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
