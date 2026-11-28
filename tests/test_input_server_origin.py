import argparse
import io
import json
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Lock, Thread
from types import SimpleNamespace

import pytest
import soundfile as sf

from jammate.input_server import Handler, https_origin


PUBLIC_ORIGIN = "https://studio.example.com"


@contextmanager
def serve(public_origin=None, *, origin_file=None, drain_file=None):
    @contextmanager
    def open_bar(session, index):
        assert (session, index) == ("jam", 2)
        yield 4, (chunk for chunk in [b"ab", b"cd"])

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.public_origins = {public_origin} if public_origin else set()
        server.public_origin_file = origin_file
        server.remote_backend_url = ""
        server.start_lock = Lock()
        server.pending_starts = 0
        server.drain_file = drain_file
        server.reference = SimpleNamespace(implementation="workspace")
        server.jam = SimpleNamespace(dispatch=lambda op, body: {"operation": op}, open_mrt2_bar=open_bar,
                                     lock=Lock(), sessions={})
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def request(port, method, path, origin=None, *, fetch_site="same-origin"):
    headers = {"Sec-Fetch-Site": fetch_site, "Content-Type": "application/json",
               "X-Forwarded-Host": "untrusted.example.com"}
    if origin is not None:
        headers["Origin"] = origin
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body="{}" if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_local_inputs_work_without_a_public_origin():
    with serve() as port:
        assert request(port, "POST", "/api/jam/status", f"http://localhost:{port}")[0] == 200
        assert request(port, "POST", "/api/jam/status")[0] == 200
        assert request(port, "POST", "/api/jam/status", PUBLIC_ORIGIN)[0] == 403


def test_exact_public_origin_accepts_api_calls_and_streams():
    with serve(PUBLIC_ORIGIN) as port:
        assert request(port, "POST", "/api/jam/status", PUBLIC_ORIGIN)[0] == 200
        assert request(port, "POST", "/api/jam/status", "https://untrusted.example.com")[0] == 403
        assert request(port, "POST", "/api/rhythm", fetch_site="cross-site")[0] == 403
        stream = "/api/jam/mrt2-stream?session=jam&bar=2"
        assert request(port, "GET", stream, PUBLIC_ORIGIN) == (200, b"abcd")
        assert request(port, "GET", stream, fetch_site="cross-site")[0] == 403
        status, encoded = request(port, "GET", stream + "&format=flac", PUBLIC_ORIGIN)
        assert status == 200
        samples, rate = sf.read(io.BytesIO(encoded), dtype="int16")
        assert rate == 48000 and samples.astype("<i2").tobytes() == b"abcd"
        if sf.check_format("OGG", "OPUS"):
            status, encoded = request(port, "GET", stream + "&format=opus", PUBLIC_ORIGIN)
            assert status == 200 and encoded.startswith(b"OggS")
            samples, rate = sf.read(io.BytesIO(encoded), dtype="int16")
            assert rate == 48000 and samples.shape == (1, 2)


def test_rotating_origin_only_accepts_the_current_name(tmp_path):
    origin_file = tmp_path / "origin"
    first = "https://previous.example.com"
    second = "https://current.example.com"
    origin_file.write_text(first)
    with serve(PUBLIC_ORIGIN, origin_file=origin_file) as port:
        assert request(port, "POST", "/api/jam/status", first)[0] == 200
        origin_file.write_text(second)
        assert request(port, "POST", "/api/jam/status", first)[0] == 403
        assert request(port, "POST", "/api/jam/status", second)[0] == 200
        assert request(port, "POST", "/api/jam/status", PUBLIC_ORIGIN)[0] == 200


def test_release_notice_and_drain_are_independent(tmp_path, monkeypatch):
    drain = tmp_path / "deploy-drain"
    available = tmp_path / "deploy-available"
    progress = tmp_path / "deploy-status.json"
    monkeypatch.setenv("JAMMATE_RELEASE", "a" * 40)
    with serve(drain_file=drain) as port:
        for phase in ("preparing", "delayed"):
            progress.write_text(json.dumps({"release": "b" * 40, "phase": phase}))
            ready = json.loads(request(port, "GET", "/api/ready")[1])
            assert ready["deployment"] == {"release": "b" * 40, "phase": phase}
            assert ready["available_release"] is None
            assert not ready["updating"]
            assert request(port, "POST", "/api/jam/start")[0] == 200

        available.write_text("b" * 40)
        status, body = request(port, "GET", "/api/ready")
        assert status == 200
        ready = json.loads(body)
        assert ready["release"] == "a" * 40
        assert ready["available_release"] == "b" * 40
        assert not ready["updating"]
        assert request(port, "POST", "/api/jam/start")[0] == 200

        drain.touch()
        assert request(port, "POST", "/api/jam/start")[0] == 503
        assert request(port, "POST", "/api/jam/poll")[0] == 200
        assert json.loads(request(port, "GET", "/api/ready")[1])["updating"]


@pytest.mark.parametrize("value", ["http://example.com", "https://", "https://example.com/",
                                    "https://user@example.com", "https://example.com?x=1"])
def test_misconfigured_origins_are_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        https_origin(value)
