# Input integration

Python 3.10+ and Node 20.11+. Commands from repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm --dir web/baseline install --frozen-lockfile
pnpm --dir web/baseline run build
pnpm --dir web/optimized install --frozen-lockfile
pnpm --dir web/optimized run build
.\.venv\Scripts\python.exe examples/make_input_examples.py
.\.venv\Scripts\python.exe -m jammate.input_server
```

Open http://127.0.0.1:4175 . Both humming versions use the same model and can import the same audio. Rhythm uses the existing Python backend. Input files are not persisted by the HTTP server. Download original recordings and events before closing the browser.

On this prepared Windows checkout, double-click `start-input-lab.cmd` after boot. It starts a hidden loopback server and opens the workbench. Logs are under `tmp/input-server*.log`. The launcher reuses a healthy server on its port; it does not reinstall packages or update source. `.venv\Scripts\python.exe scripts/start_input_lab.py --port 4177 --no-browser` starts another port for testing.

Use `web/appendix.html` for the comparison page; after starting the server it is also at `/appendix.html`. The static appendix can be opened directly, but the model lab requires the HTTP server. `web/baseline` is unchanged from the frozen baseline commit; the complete pre-optimization project is also preserved as `integration-baseline`. Baseline's optimized-version/appendix links were placeholders at that checkpoint. Final checkout provides both pages together.

Validation (no physical microphone):

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=tmp/pytest-input-check
node --test web/baseline/tests/*.test.mjs
node --test web/optimized/tests/*.test.mjs
# Set PLAYWRIGHT_MODULE_PATH to your installed Playwright package when it is
# outside normal module resolution; browser scripts currently use msedge.
$env:INPUT_LAB_URL='http://127.0.0.1:4175'
node tests/input-browser.mjs
node tests/input-lifecycle.mjs
node tests/humming-benchmark.mjs
```

Use a fresh project-specific pytest temporary path if the system temp directory has restrictive Windows ACLs. The default temp directory failed during this run; the complete suite passed with a fresh workspace temp directory. Python dependency versions used are saved in `requirements-input.lock.txt`; JS lockfiles are retained in both versions. Browser tests require Playwright and Edge; end-user inference does not require Playwright.

The five synthetic humming WAVs and expected notes are committed in `web/fixtures`; regenerate with `python examples/make_humming_benchmark.py`. The benchmark writes measured results to `docs/integration/humming-benchmark.json`. The appendix uses a frozen copy in `web/appendix-data.js` so it can display offline; update that copy deliberately when accepting a new benchmark.

CLI: `python -m jammate.input_cli rhythm recording.wav --out event.json --session-id my-session --started-at 12.5`; use `--count-in 0 --tempo 100 --origin 0` only when BPM and bar origin are known. `humming-json` adapts existing Hum Lab exports to the same event structure. Existing band-generation commands remain available independently.

The local lab provides explicit input routes for controlled debugging. Automatic speech/humming/rhythm switching remains future work, not a finished product feature. No combined song is generated.
