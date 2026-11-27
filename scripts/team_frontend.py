"""Serve this checkout's Studio and send its API calls to the Mac team backend."""
import argparse
from functools import partial
import getpass
from http.client import HTTPSConnection, HTTPException
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1] / "web"
COOKIE = "__Host-jammate_access"


class TeamBackend:
    def __init__(self, invitation):
        self.url = urlsplit(invitation)
        if (self.url.scheme != "https" or not self.url.hostname or self.url.username is not None
                or self.url.path != "/team-login" or self.url.query or not self.url.fragment):
            raise ValueError("请使用完整的 HTTPS 团队邀请链接。")
        self.origin = "https://" + self.url.netloc
        self.lock = threading.Lock()
        self.cookie = None

    def connect(self):
        return HTTPSConnection(self.url.hostname, self.url.port, timeout=120)

    def login(self, previous=None):
        with self.lock:
            if self.cookie and self.cookie != previous:
                return self.cookie
            connection = self.connect()
            try:
                connection.request("POST", "/team-login", body=b"",
                                   headers={"X-JamMate-Invite": self.url.fragment, "Origin": self.origin})
                response = connection.getresponse()
                cookies = SimpleCookie(response.getheader("Set-Cookie", ""))
                if response.status != 204 or COOKIE not in cookies or not cookies[COOKIE]["secure"]:
                    raise OSError("Mac 没有接受团队邀请，请确认链接仍然有效。")
                self.cookie = f"{COOKIE}={cookies[COOKIE].value}"
                return self.cookie
            finally:
                connection.close()


class Frontend(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            self.forward("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        self.forward("POST")

    def json_error(self, status, message):
        body = json.dumps({"error": message}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def forward(self, method):
        port = self.server.server_port
        if (self.headers.get("Sec-Fetch-Site") == "cross-site"
                or self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}")):
            self.json_error(403, "请从本地 JamMate 页面发起请求。")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16 * 1024 * 1024:
                raise ValueError
        except ValueError:
            self.json_error(413, "请求超过允许的大小。")
            return
        body = self.rfile.read(length) if method == "POST" else None
        backend = self.server.backend
        upstream_started = False
        try:
            cookie = backend.login()
            for attempt in range(2):
                upstream = backend.connect()
                try:
                    upstream.request(method, self.path, body=body, headers={
                        "Cookie": cookie, "Origin": backend.origin,
                        "Content-Type": self.headers.get("Content-Type", "application/json"),
                    })
                    response = upstream.getresponse()
                    if response.status == 401 and not attempt:
                        cookie = backend.login(cookie)
                        continue
                    self.send_response(response.status)
                    for header in ("Content-Type", "Content-Length", "Cache-Control"):
                        if value := response.getheader(header):
                            self.send_header(header, value)
                    self.end_headers()
                    upstream_started = True
                    while chunk := response.read1(64 * 1024):
                        self.wfile.write(chunk)
                    return
                finally:
                    upstream.close()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (OSError, HTTPException) as error:
            if upstream_started:
                self.close_connection = True
            else:
                self.json_error(502, f"Mac 后端连接失败：{error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4188)
    parser.add_argument("--invite-file", type=Path, help="文本文件第一行的固定团队邀请链接")
    args = parser.parse_args()
    invite = (args.invite_file.read_text(encoding="utf-8-sig").partition("\n")[0] if args.invite_file
              else getpass.getpass("粘贴 JamMate 团队邀请链接："))
    try:
        backend = TeamBackend(invite.strip())
        backend.login()
    except (ValueError, OSError, HTTPException) as error:
        parser.exit(1, f"{error}\n")
    with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Frontend, directory=str(ROOT))) as server:
        server.backend = backend
        print(f"本地 Studio：http://127.0.0.1:{server.server_port}/jam.html", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
