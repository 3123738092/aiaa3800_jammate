"""Synthetic test input: a clapped count-in followed by a hummed phrase.

Lets the whole pipeline run (and be tested) without a microphone recording.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from jammate.music import midi_to_hz
from jammate.render.synth import SR

# A short phrase in E major, so the band has to leave its default C major.
DEMO_MELODY: tuple[tuple[int, float], ...] = (
    (64, 1.0), (66, 0.5), (68, 0.5), (71, 1.0), (68, 1.0), (66, 0.5), (64, 1.5),
)
_CLAP_S = 0.03
_FADE_S = 0.02
_VIBRATO_SEMITONES = 0.08


def _clap(sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(_CLAP_S * sr)
    return 0.8 * rng.standard_normal(n) * np.exp(-np.linspace(0.0, 6.0, n))


def _hummed_note(pitch: int, seconds: float, sr: int) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    freq = midi_to_hz(pitch) * 2 ** (_VIBRATO_SEMITONES * np.sin(2 * np.pi * 5.0 * t) / 12)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    tone = 0.5 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    fade = min(t.size // 2, int(_FADE_S * sr))
    envelope = np.ones(t.size)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[t.size - fade:] = np.linspace(1.0, 0.0, fade)
    return 0.6 * tone * envelope


def synth_hum(melody: Sequence[tuple[int, float]], tempo_bpm: float = 100.0, sr: int = SR,
              count_in: int = 4, articulation: float = 0.9, seed: int = 0) -> np.ndarray:
    """`count_in` claps, then `melody` as (MIDI pitch, beats) hummed notes."""
    rng = np.random.default_rng(seed)
    seconds_per_beat = 60.0 / tempo_bpm
    total_beats = count_in + sum(beats for _, beats in melody) + 1
    out = np.zeros(int(total_beats * seconds_per_beat * sr))
    for k in range(count_in):
        clap = _clap(sr, rng)
        start = int(k * seconds_per_beat * sr)
        out[start:start + clap.size] += clap
    beat = float(count_in)
    for pitch, beats in melody:
        note = _hummed_note(pitch, beats * seconds_per_beat * articulation, sr)
        start = int(beat * seconds_per_beat * sr)
        out[start:start + note.size] += note
        beat += beats
    return (0.9 * out / np.max(np.abs(out))).astype(np.float32)
