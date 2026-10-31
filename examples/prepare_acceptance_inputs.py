"""Freeze a fresh text/audio batch before inference. No oracle enters recognition."""
import argparse
import hashlib
import json
from pathlib import Path
import wave

import numpy as np


def prepare(output, cases):
    output.mkdir(parents=True, exist_ok=False)
    (output / "inputs").mkdir()
    data = json.loads(cases.read_text(encoding="utf-8"))
    rng = np.random.default_rng(data["seed"])
    sr = data["sample_rate"]
    files = {}

    def save(name, audio):
        path = output / "inputs" / (name + ".wav")
        with wave.open(str(path), "wb") as f:
            f.setparams((1, 2, sr, len(audio), "NONE", "not compressed"))
            f.writeframes((np.clip(audio, -.98, .98) * 32767).astype("<i2").tobytes())
        files[name] = {"path": path.relative_to(output).as_posix(),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source": "synthetic_audio"}

    for case in data["rhythm"]:
        period = 60 / case["bpm"]
        intervals = case.get("count_in_intervals", [period] * 3)
        count = [.3]
        for interval in intervals:
            count.append(count[-1] + interval)
        origin = count[-1] + period
        audio = np.zeros(round((origin + period * 4 + .12) * sr))
        if not case.get("silent"):
            times = count + [origin + b * period for b in case["beats"]]
            for index, (time, accent) in enumerate(zip(times, [1] * 4 + case["accents"])):
                time += rng.uniform(-case.get("jitter_s", 0), case.get("jitter_s", 0)) if index >= 4 else 0
                start, size = round(time * sr), round(.04 * sr)
                burst = .24 * accent * rng.standard_normal(size) * np.exp(-np.linspace(0, 7, size))
                audio[start:start + size] += burst
            audio += rng.normal(0, case.get("noise", 0), len(audio))
        save(case["id"], audio)
    for case in data["humming"]:
        audio = np.zeros(round(case["duration_s"] * sr))
        for pitch, start, duration in case["notes"]:
            t = np.arange(round(duration * sr)) / sr
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            phase = 2 * np.pi * np.cumsum(frequency * 2 ** (.09 * np.sin(2 * np.pi * 5.1 * t) / 12)) / sr
            tone = sum(amplitude * np.sin(harmonic * phase) for harmonic, amplitude in ((1, 1), (2, .30), (3, .12)))
            ramp = min(round(.03 * sr), len(tone) // 2)
            tone[:ramp] *= np.linspace(0, 1, ramp)
            tone[-ramp:] *= np.linspace(1, 0, ramp)
            offset = round(start * sr)
            audio[offset:offset + len(tone)] += case.get("gain", .26) * tone
        audio += rng.normal(0, case.get("noise", 0), len(audio))
        save(case["id"], audio)
    data["files"] = files
    data["cases_source_sha256"] = hashlib.sha256(cases.read_bytes()).hexdigest()
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "manifest.sha256").write_text(hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n")
    print(f"Frozen: 16 text turns + {len(data['rhythm'])} rhythm WAV + {len(data['humming'])} humming WAV + 1 multimodal turn -> {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("acceptance_cases.json"))
    args = parser.parse_args()
    prepare(args.out, args.cases)
