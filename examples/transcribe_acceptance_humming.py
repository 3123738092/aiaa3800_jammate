"""Exercise the unchanged humming handoff's real UI and Basic Pitch model.

Requires Python Playwright + Chromium. Serve only the local dist; no microphone.
"""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import time

from playwright.sync_api import sync_playwright


def transcribe(batch, dist, *, output=None, disable_inferred_onsets=False, onset_threshold=None):
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((batch / "manifest.json").read_bytes()).hexdigest() == (batch / "manifest.sha256").read_text().strip()
    output = output or batch / "humming_inference"
    output.mkdir(parents=True, exist_ok=False)
    bundle = (dist / "app.js").read_bytes()
    served = bundle.decode("utf-8")
    replacements = {}
    if disable_inferred_onsets:
        replacements["inferOnsets:!0"] = "inferOnsets:!1"
    if onset_threshold is not None:
        if onset_threshold != .5:
            raise ValueError("diagnostic threshold must be .5")
        replacements["onsetThreshold:.25"] = "onsetThreshold:.5"
    for old, new in replacements.items():
        if served.count(old) != 1:
            raise ValueError("diagnostic override does not match the pinned handoff bundle")
        served = served.replace(old, new)
    served_bytes = served.encode("utf-8")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/app.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(served_bytes)))
                self.end_headers()
                self.wfile.write(served_bytes)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(dist.resolve())))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    rows = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(accept_downloads=True)
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
            for case in manifest["humming"]:
                path = batch / manifest["files"][case["id"]]["path"]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["files"][case["id"]]["sha256"]
                page.goto(origin, wait_until="networkidle")
                page.wait_for_selector("#model-badge.ready", timeout=90000)
                page.locator("#file").set_input_files(str(path.resolve()))
                page.wait_for_selector("#quality-panel:visible")
                quality = page.locator("#quality-panel").get_attribute("data-quality")
                row = {"id": case["id"], "quality": quality,
                       "quality_message": page.locator("#quality-summary").inner_text()}
                if quality != "rejected":
                    started = time.monotonic()
                    page.locator("#convert").click()
                    page.wait_for_selector("#result-content:visible", timeout=180000)
                    row["inference_s"] = round(time.monotonic() - started, 3)
                    for variant in ("raw", "cleaned"):
                        page.locator("#variant").select_option(variant)
                        with page.expect_download() as download:
                            page.locator("#download-json").click()
                        download.value.save_as(str(output / f"{case['id']}_{variant}.json"))
                    with page.expect_download() as download:
                        page.locator("#download-midi").click()
                    download.value.save_as(str(output / f"{case['id']}_cleaned.mid"))
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        model_files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dist / "model").iterdir() if p.is_file()}
        (output / "report.json").write_text(json.dumps({"source": str(dist), "model_sha256": model_files,
            "original_bundle_sha256": hashlib.sha256(bundle).hexdigest(), "served_bundle_sha256": hashlib.sha256(served_bytes).hexdigest(),
            "diagnostic_overrides": {"disable_inferred_onsets": disable_inferred_onsets, "onset_threshold": onset_threshold},
            "inference": "real Basic Pitch in headless Chromium; synthetic imported WAV", "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--disable-inferred-onsets", action="store_true", help="diagnostic only; original handoff files stay unchanged")
    parser.add_argument("--onset-threshold", type=float, choices=(.5,), help="diagnostic 0.5 vs original 0.25")
    args = parser.parse_args()
    transcribe(args.batch, args.dist, output=args.out, disable_inferred_onsets=args.disable_inferred_onsets, onset_threshold=args.onset_threshold)
