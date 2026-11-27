"""Loopback server for local use or an authenticated HTTPS reverse proxy."""
import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import closing
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf
from jammate.browser_jam import BrowserJamService
from jammate.opening_mic import OpeningMic
from jammate.input_event import perceive_rhythm, validate_event
from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_rhythm
from jammate.input_band import render_clip
from jammate.reference_bridge import ReferenceBridge, SERVICE_ID
from jammate.rhythm import RhythmContext

ROOT = Path(__file__).resolve().parents[2] / "web"


def https_origin(value):
    """Accept only an exact HTTPS origin, without a path or credentials."""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and not parsed.path and not parsed.query and not parsed.fragment
                and value == f"https://{parsed.netloc}" and parsed.port != 0
                and not any(char.isspace() for char in value) and "\\" not in value):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected an HTTPS origin such as https://jam.example.com")


class Handler(SimpleHTTPRequestHandler):
    def same_workspace(self):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        port = self.server.server_port
        origins = {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                   *getattr(self.server, "public_origins", ())}
        origin = self.headers.get("Origin")
        if origin in origins:
            return True
        origin_file = getattr(self.server, "public_origin_file", None)
        if origin_file is None:
            return False
        try:
            return origin == https_origin(origin_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError, argparse.ArgumentTypeError):
            return False

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/jam/mrt2-stream":
            if not self.same_workspace():
                self.json_response(403, {"error": "请从工作台页面发起请求。"})
            elif self.server.remote_backend_url:
                self.proxy_remote("GET", self.path)
            else:
                self.send_mrt2_bar(query)
        elif self.path == "/api/ready":
            with self.server.start_lock, self.server.jam.lock:
                active = sum(not session.closed and monotonic() - session.last_seen <= 90
                             for session in self.server.jam.sessions.values())
                pending = self.server.pending_starts
            drain = self.server.drain_file
            available = None
            deployment = None
            if drain:
                try:
                    value = drain.with_name("deploy-available").read_text(encoding="ascii").strip()
                    if re.fullmatch(r"[a-f0-9]{40}", value):
                        available = value
                except (OSError, UnicodeError):
                    pass
                try:
                    progress = json.loads(drain.with_name("deploy-status.json").read_text(encoding="ascii"))
                    if (isinstance(progress, dict) and isinstance(progress.get("release"), str)
                            and re.fullmatch(r"[a-f0-9]{40}", progress["release"])
                            and progress.get("phase") in ("preparing", "delayed")):
                        deployment = {"release": progress["release"], "phase": progress["phase"]}
                except (OSError, ValueError):
                    pass
            self.json_response(200, {"service": SERVICE_ID, "root": str(ROOT.parent),
                                     "implementation": self.server.reference.implementation,
                                     "release": os.getenv("JAMMATE_RELEASE"),
                                     "deployment": deployment,
                                     "available_release": available,
                                     "updating": bool(drain and drain.is_file()),
                                     "active_sessions": active, "pending_starts": pending})
        else:
            super().do_GET()

    def send_mrt2_bar(self, query):
        response_started = False
        try:
            values = parse_qs(query, max_num_fields=2, strict_parsing=True)
            session, = values["session"]
            index, = values["bar"]
            with self.server.jam.open_mrt2_bar(session, int(index)) as (length, chunks):
                with closing(chunks):
                    first = next(chunks)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    response_started = True
                    self.wfile.write(first)
                    for chunk in chunks:
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (KeyError, ValueError, ImportError, RuntimeError, OSError, StopIteration) as error:
            if response_started:
                self.log_error("MRT2 stream interrupted: %s", error)
                self.close_connection = True
            else:
                self.json_response(400, {"error": f"MRT2 流式生成失败：{error}"})

    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def start_jam(self, body):
        with self.server.start_lock:
            drain_file = self.server.drain_file
            if drain_file and drain_file.is_file():
                self.json_response(503, {"error": "JamMate 正在更新，稍等几秒后重试。"})
                return
            self.server.pending_starts += 1
        try:
            self.json_response(200, self.server.jam.dispatch("start", body))
        finally:
            with self.server.start_lock:
                self.server.pending_starts -= 1

    def proxy_remote(self, method, path, body=None):
        """Forward JamMate operations through the configured SSH tunnel."""
        target = urljoin(self.server.remote_backend_url.rstrip("/") + "/", path.lstrip("/"))
        request = Request(target, data=body, method=method,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urlopen(request, timeout=180) as response:
                self.send_response(response.status)
                for name in ("Content-Type", "Content-Length", "Cache-Control"):
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.end_headers()
                while chunk := response.read(64 * 1024):
                    self.wfile.write(chunk)
        except HTTPError as error:
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(error.read())
        except (URLError, TimeoutError, OSError) as error:
            if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                self.close_connection = True
                return
            self.json_response(502, {"error": "Mac 后端不可达；请检查 SSH 4215 端口转发。"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path.startswith("/api/") and not self.same_workspace():
                # Drain small rejected requests so closing TCP does not reset the response.
                if 0 < length <= 4096:
                    self.rfile.read(length)
                self.json_response(403, {"error": "请从工作台页面发起请求。"})
                return
            if not 0 < length <= 16 * 1024 * 1024:
                raise ValueError("request must be 1 byte to 16 MB")
            if self.path.startswith(("/api/reference/", "/api/jam/")):
                if self.headers.get_content_type() != "application/json":
                    self.json_response(415, {"error": "请提交 JSON。"})
                    return
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("请求必须为 JSON 对象。")
                if self.path.startswith("/api/jam/"):
                    if self.server.remote_backend_url:
                        self.proxy_remote("POST", self.path, json.dumps(body, ensure_ascii=False).encode())
                        return
                    operation = self.path.removeprefix("/api/jam/")
                    if operation == "opening-mic":
                        self.json_response(200, self.server.opening_mic.recognize(body))
                        return
                    if operation not in {"status", "start", "poll", "feedback", "transcribe", "capture", "preview", "timbres", "stop", "debug", "studio-input", "studio-classify", "studio-suggestion"}:
                        self.json_response(404, {"error": "unknown Jam endpoint"})
                        return
                    if operation == "start":
                        self.start_jam(body)
                        return
                    self.json_response(200, self.server.jam.dispatch(operation, body))
                else:
                    operation = self.path.removeprefix("/api/reference/")
                    if operation not in {"rhythm/analyze", "rhythm/create", "text/status", "text/turn", "style"}:
                        self.json_response(404, {"error": "unknown reference endpoint"})
                        return
                    if operation in {"text/status", "text/turn"} and self.server.remote_backend_url:
                        self.proxy_remote("POST", self.path, json.dumps(body, ensure_ascii=False).encode())
                        return
                    self.json_response(200, self.server.reference.request(operation, body))
            elif self.path in ("/api/rhythm", "/api/rhythm/v1"):
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
            elif self.path in ("/api/band", "/api/create"):
                self.json_response(200, render_clip(json.loads(self.rfile.read(length)),
                    creative=self.path == "/api/create",
                    fluidsynth=self.server.fluidsynth, soundfont=self.server.soundfont))
            else:
                self.json_response(404, {"error": "unknown endpoint"})
        except (ValueError, KeyError, TypeError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            reply = {"error": str(exc)}
            if getattr(exc, "report", None) is not None:
                reply["capabilities"] = exc.report
            self.json_response(400, reply)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4185)
    parser.add_argument("--public-origin", type=https_origin, action="append", default=[],
                        help="exact external HTTPS origin served by the authenticated reverse proxy, repeatable")
    parser.add_argument("--implementation", choices=("workspace", "pinned"), default="workspace",
                        help="workspace loads new backends; pinned reproduces a63a0f5")
    parser.add_argument("--fluidsynth", default=os.environ.get("JAMMATE_FLUIDSYNTH") or shutil.which("fluidsynth"))
    parser.add_argument("--soundfont", default=os.environ.get("JAMMATE_SOUNDFONT"))
    args = parser.parse_args()
    try:
        public_origins = {*args.public_origin, *(
            https_origin(origin.strip()) for origin in os.getenv("JAMMATE_PUBLIC_ORIGIN", "").split(",")
            if origin.strip()
        )}
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.fluidsynth and not args.soundfont:
        import pretty_midi
        bundled = ROOT.parent / "examples" / "backend_inputs" / "TimGM6mb.sf2"
        args.soundfont = bundled if bundled.is_file() else Path(pretty_midi.__file__).with_name("TimGM6mb.sf2")
    if args.fluidsynth and not all(Path(p).is_file() for p in (args.fluidsynth, args.soundfont)):
        parser.error("FluidSynth and SoundFont must be existing files")
    if args.soundfont and not args.fluidsynth:
        parser.error("a SoundFont requires FluidSynth")
    with (
        closing(OpeningMic()) as opening_mic,
        closing(BrowserJamService(fluidsynth=args.fluidsynth, soundfont=args.soundfont, audio_router=opening_mic)) as jam,
        ReferenceBridge(ROOT.parent, scratch=ROOT.parent / "tmp", fluidsynth=args.fluidsynth,
                        soundfont=args.soundfont, implementation=args.implementation) as reference,
        ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(ROOT))) as server,
    ):
        server.fluidsynth, server.soundfont = args.fluidsynth, args.soundfont
        server.public_origins = public_origins
        server.start_lock = Lock()
        server.pending_starts = 0
        drain_file = os.getenv("JAMMATE_DRAIN_FILE")
        server.drain_file = Path(drain_file) if drain_file else None
        origin_file = os.getenv("JAMMATE_PUBLIC_ORIGIN_FILE")
        server.public_origin_file = Path(origin_file) if origin_file else None
        server.remote_backend_url = os.environ.get("JAMMATE_REMOTE_BACKEND_URL", "").strip()
        server.reference = reference
        server.opening_mic = opening_mic
        server.jam = jam
        print(f"JamMate Input Lab: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
