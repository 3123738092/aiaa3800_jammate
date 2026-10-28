"""Matched 100-BPM rhythms, three modes, and a synthetic clap round trip.

Run from the project root: python examples/rhythm_demo.py
Outputs go to demo_out/rhythm, leaving the original hum demo untouched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf

from jammate.analysis.rhythm import analyze_rhythm_audio
from jammate.pipeline_rhythm import jam_rhythm
from jammate.render.midi_out import write_midi
from jammate.render.synth import SR
from jammate.rhythm import RhythmMode, load_rhythm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("demo_out/rhythm"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"tempo_bpm": 100, "bars": 4, "midi_measurements": {}}
    for name in ("a", "b"):
        context = load_rhythm(Path(__file__).with_name(f"rhythm_{name}.json"))
        for mode in RhythmMode:
            stem = args.out / f"{name}_{mode.value}"
            result = jam_rhythm(context, bars=4, mode=mode)
            sf.write(stem.with_suffix(".wav"), result.audio, SR)
            write_midi(result.notes, context.tempo_bpm, stem.with_suffix(".mid"))
            # Re-read the exported artifact, measuring actual MIDI event times.
            midi = pretty_midi.PrettyMIDI(str(stem.with_suffix(".mid")))
            bass = next(t for t in midi.instruments if t.name == "bass")
            first_bar = [round(n.start * context.tempo_bpm / 60, 3)
                         for n in bass.notes if n.start * context.tempo_bpm / 60 < 4 - .001]
            report["midi_measurements"][f"{name}_{mode.value}"] = {
                "first_bar_bass_onsets": first_bar, "bass_notes": len(bass.notes),
                "tracks": [t.name for t in midi.instruments],
            }
            if mode is RhythmMode.FOLLOW:
                assert np.allclose(first_bar, context.onset_beats, atol=.01)

    context = load_rhythm(Path(__file__).with_name("rhythm_a.json"))
    period, origin = .6, 2.7
    times = [.3 + i * period for i in range(4)] + [origin + b * period for b in context.onset_beats]
    strengths = [1] * 4 + list(context.accents)
    audio = np.zeros(round((origin + 4 * period + .05) * SR), dtype=np.float32)
    rng = np.random.default_rng(42)
    for time_s, strength in zip(times, strengths):
        start, size = round(time_s * SR), round(.03 * SR)
        burst = .25 * strength * rng.standard_normal(size) * np.exp(-np.linspace(0, 6, size))
        audio[start:start + size] += burst.astype(np.float32)
    recording = args.out / "claps_a.wav"
    sf.write(recording, audio, SR)
    # Re-read quantized WAV samples rather than analyzing the source array.
    decoded, sr = sf.read(recording, dtype="float32")
    recovered = analyze_rhythm_audio(decoded, sr)
    assert recovered.onset_beats == context.onset_beats
    assert abs(recovered.tempo_bpm - 100) < 2
    assert recovered.confidence >= .65
    (args.out / "extracted_a.json").write_text(json.dumps(recovered.to_dict(), indent=2) + "\n")
    report["audio_roundtrip"] = recovered.to_dict()
    measured = report["midi_measurements"]
    assert measured["a_follow"]["first_bar_bass_onsets"] != measured["b_follow"]["first_bar_bass_onsets"]
    for name in ("a", "b"):
        assert measured[f"{name}_simplify"]["bass_notes"] < measured[f"{name}_follow"]["bass_notes"]
        assert measured[f"{name}_follow"]["bass_notes"] < measured[f"{name}_vary"]["bass_notes"]
    report["checks_passed"] = True
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
