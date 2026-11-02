"""Loopback-only development server for the two existing input implementations."""
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf
from jammate.input_event import perceive_rhythm, validate_event
from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_rhythm
from jammate.input_band import render_clip
from jammate.rhythm import RhythmContext

ROOT = Path(__file__).resolve().parents[2] / "web"


class Handler(SimpleHTTPRequestHandler):
    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16 * 1024 * 1024:
                raise ValueError("request must be 1 byte to 16 MB")
            if self.path in ("/api/rhythm", "/api/rhythm/v1"):
                body = json.loads(self.rfile.read(length))
                audio, sr = sf.read(io.BytesIO(base64.b64decode(body["wav"], validate=True)), dtype="float32", always_2d=True)
                if len(audio)/sr > 30 or sr < 8000 or sr > 96000:
                    raise ValueError("use audio <=30 seconds at 8-96 kHz")
                event = perceive_rhythm(np.mean(audio, axis=1), sr, session_id=body["session_id"],
                    started_at=body.get("started_at", 0), count_in=body.get("count_in", 4),
                    tempo_hint=body.get("tempo"), beat_origin_s=body.get("origin"))
                if self.path.endswith("/v1"):
                    event = from_rhythm(
                        RhythmContext(**event["payload"]["rhythm"]), audio_duration_s=len(audio) / sr,
                        session_id=event["session_id"], event_id=event["event_id"], started_at=event["started_at"],
                        tempo_source="detected" if body.get("count_in", 4) else "manual",
                        confidence_kind="grid_alignment_heuristic",
                    ).to_dict()
                self.json_response(200, event)
            elif self.path == "/api/validate":
                event = json.loads(self.rfile.read(length))
                if isinstance(event, dict) and "schema_version" in event:
                    event = InputEvent.from_dict(event).to_dict()
                else:
                    event = validate_event(event)
                self.json_response(200, event)
            elif self.path == "/api/band":
                self.json_response(200, render_clip(json.loads(self.rfile.read(length)),
                    fluidsynth=self.server.fluidsynth, soundfont=self.server.soundfont))
            else:
                self.json_response(404, {"error": "unknown endpoint"})
        except (ValueError, KeyError, TypeError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            self.json_response(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4185)
    parser.add_argument("--fluidsynth", default=os.environ.get("JAMMATE_FLUIDSYNTH") or shutil.which("fluidsynth"))
    parser.add_argument("--soundfont", default=os.environ.get("JAMMATE_SOUNDFONT"))
    args = parser.parse_args()
    if args.fluidsynth and not args.soundfont:
        import pretty_midi
        args.soundfont = Path(pretty_midi.__file__).with_name("TimGM6mb.sf2")
    if args.fluidsynth and not all(Path(p).is_file() for p in (args.fluidsynth, args.soundfont)):
        parser.error("FluidSynth and SoundFont must be existing files")
    if args.soundfont and not args.fluidsynth:
        parser.error("a SoundFont requires FluidSynth")
    with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(ROOT))) as server:
        server.fluidsynth, server.soundfont = args.fluidsynth, args.soundfont
        print(f"JamMate Input Lab: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
