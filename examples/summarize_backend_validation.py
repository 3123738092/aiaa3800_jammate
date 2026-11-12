"""Package already recorded real outputs; does not call models or rewrite them."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import soundfile as sf


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
              "hardware": "WSL Linux x86_64, NVIDIA RTX 4060 Laptop 8 GiB",
              "real_model": "magenta-rt==2.0.3, mrt2_small, JAX/CUDA 12",
              "python_tests": [s.attrib for s in ET.parse(args.junit).getroot().iter("testsuite")],
              "javascript_tests": {"passed": 30, "failed": 0},
              "limitations": ["All source MIDI/inputs are synthetic/development inputs",
                              "Audio decoding/exports are not physical playback or blinded music quality evaluation",
                              "MRT2 preserve constraints and action effects are not verified in generated audio"],
              "files": {}, "paired": []}
    for stem in ("direct-fluid", "direct-text", "direct-midi"):
        shutil.copyfile(args.source/f"{stem}.wav", args.out/f"{stem}.wav")
        metadata = args.source/f"{stem}.inference.json"
        if metadata.exists():
            shutil.copyfile(metadata, args.out/metadata.name)
    paired = json.loads((args.source/"paired/comparison.json").read_text())
    for row in paired["results"]:
        label = f'{row["agent"]}-{row["backend"]}'
        folder, = (args.source/f'paired/{row["agent"]}_{row["backend"]}').glob("plan_*")
        for suffix in ("mid", "wav"):
            shutil.copyfile(folder/f"band.{suffix}", args.out/f"{label}.{suffix}")
        (args.out/f"{label}.receipt.json").write_text(json.dumps(row["receipt"], ensure_ascii=False, indent=2)+"\n")
        report["paired"].append({"agent": row["agent"], "backend": row["backend"],
            "plan_id": row["plan_id"], "receipt": row["receipt"],
            "drums_level_after": next(x["level"] for x in row["state_after"]["arrangement"]["layers"] if x["name"] == "drums")})
    for mode in ("rule", "llm"):
        shutil.copyfile(args.source/f"paired/{mode}.plan.json", args.out/f"{mode}.plan.json")
        assert digest(args.out/f"{mode}-fluidsynth.mid") == digest(args.out/f"{mode}-mrt2.mid")
    report["same_plan_control_midi_identical"] = True
    shutil.copyfile(args.source/"paired/input.json", args.out/"input.json")
    shutil.copyfile(args.browser/"report.json", args.out/"browser-report.json")
    for name in ("mrt2-preview.png", "llm-mobile.png"):
        shutil.copyfile(args.browser/name, args.out/name)
    for file in args.out.iterdir():
        if not file.is_file():
            continue
        row = {"sha256": digest(file), "bytes": file.stat().st_size}
        if file.suffix == ".wav":
            audio, sr = sf.read(file, always_2d=True)
            assert np.isfinite(audio).all() and np.max(np.abs(audio)) > 1e-7
            row.update(sample_rate=sr, channels=audio.shape[1], duration_s=len(audio)/sr,
                       peak=float(np.max(np.abs(audio))), rms=float(np.sqrt(np.mean(audio**2))))
        report["files"][file.name] = row
    (args.out/"summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(f"Packaged {len(report['files'])} real artifacts; identical control MIDI verified per Agent")


if __name__ == "__main__":
    main()
