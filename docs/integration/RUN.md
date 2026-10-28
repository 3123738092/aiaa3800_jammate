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

CLI: `python -m jammate.input_cli rhythm recording.wav --out event.json --session-id my-session --started-at 12.5`; use `--count-in 0 --tempo 100 --origin 0` only when BPM and bar origin are known. `humming-json` adapts existing Hum Lab exports to the same event structure. Existing band-generation commands remain available independently.

The local lab provides explicit input routes for controlled debugging. Automatic speech/humming/rhythm switching remains future work, not a finished product feature. No combined song is generated.
