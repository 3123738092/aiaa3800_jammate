"""Generate a known 6-second clap input and its expected rhythm.

Run from the project root: python examples/make_debug_input.py
Then pass the generated WAV to ``python -m jammate rhythm-audio``.
This fixture checks the file pipeline, not a real microphone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("demo_out/debug_rhythm"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sr, bpm, duration_s = 44100, 100.0, 6.0
    period = 60.0 / bpm
    count_in = [round(.6 + i * period, 6) for i in range(4)]
    origin = 3.0
    beats, accents = [0.0, 1.0, 1.5, 3.0], [1.0, .55, .8, 1.0]
    rhythm_times = [round(origin + b * period, 6) for b in beats]

    audio = np.zeros(round(duration_s * sr), dtype=np.float32)
    rng = np.random.default_rng(3800)
    time = np.arange(round(.06 * sr)) / sr
    noise = rng.standard_normal(len(time))
    clap = ((noise - .8 * np.r_[0, noise[:-1]])
            * (1 - np.exp(-time / .0007)) * np.exp(-time / .01))
    clap = (clap / np.max(np.abs(clap)) * .7).astype(np.float32)
    for time_s, gain in zip(count_in + rhythm_times, [.65] * 4 + accents):
        start = round(time_s * sr)
        audio[start:start + len(clap)] += gain * clap

    source = args.out / "input_claps_100bpm.wav"
    sf.write(source, audio, sr, subtype="PCM_16")
    expected = {
        "description": "Four steady count-in claps, then one 4/4 rhythm bar.",
        "sample_rate": sr, "duration_s": duration_s,
        "tempo_bpm": bpm, "beat_origin_s": origin,
        "count_in_times_s": count_in, "rhythm_times_s": rhythm_times,
        "onset_beats": beats, "relative_accents": accents,
        "rhythm_bar_end_s": origin + 4 * period, "seed": 3800,
        "note": "Source timings are exact; decoded BPM/onsets may differ slightly due to analysis frames.",
    }
    (args.out / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {source} and expected.json")


if __name__ == "__main__":
    main()
