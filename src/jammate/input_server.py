"""Loopback-only development server for the two existing input implementations."""
import argparse
import io
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf
from jammate.input_event import perceive_rhythm, validate_event

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
            if self.path == "/api/rhythm":
                body = json.loads(self.rfile.read(length))
                import base64
                audio, sr = sf.read(io.BytesIO(base64.b64decode(body["wav"], validate=True)), dtype="float32", always_2d=True)
                if len(audio)/sr > 30 or sr < 8000 or sr > 96000:
                    raise ValueError("use audio <=30 seconds at 8-96 kHz")
                event = perceive_rhythm(np.mean(audio, axis=1), sr, session_id=body["session_id"],
                    started_at=body.get("started_at", 0), count_in=body.get("count_in", 4),
                    tempo_hint=body.get("tempo"), beat_origin_s=body.get("origin"))
                self.json_response(200, event)
            elif self.path == "/api/validate":
                self.json_response(200, validate_event(json.loads(self.rfile.read(length))))
            else:
                self.json_response(404, {"error": "unknown endpoint"})
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            self.json_response(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4175)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(ROOT))) as server:
        print(f"JamMate Input Lab: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
