"""A tiny additive/noise synthesizer so the jam is audible without a SoundFont.

Sound quality is deliberately basic: it exists so the demo and tests run on
any machine. For nicer sound, export MIDI (`midi_out.py`) and render it with
a SoundFont or a DAW. All output is mono float32 within [-1, 1].
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.signal import lfilter

from jammate.music import Note, midi_to_hz

SR = 22050
PEAK_LIMIT = 0.98
TAIL_S = 1.5  # room after the last note for releases and the crash cymbal
_GAINS = {"drums": 0.8, "bass": 0.55, "keys": 0.16, "lead": 0.32, "user": 0.3}


def _time(seconds: float, sr: int) -> np.ndarray:
    return np.arange(int(round(seconds * sr))) / sr


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n)


def _envelope(t: np.ndarray, attack_s: float, hold_s: float, release_s: float,
              sustain: float = 1.0, decay_tau_s: float | None = None) -> np.ndarray:
    env = np.minimum(1.0, t / attack_s)
    if decay_tau_s is not None:
        env = env * (sustain + (1.0 - sustain) * np.exp(-t / decay_tau_s))
    after = t > hold_s
    env[after] *= np.exp(-(t[after] - hold_s) * 5.0 / release_s)
    return env


def _kick(sr: int) -> np.ndarray:
    t = _time(0.35, sr)
    freq = 45.0 + 75.0 * np.exp(-t * 30.0)
    return np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 9.0)


def _snare(sr: int) -> np.ndarray:
    t = _time(0.2, sr)
    return 0.7 * _noise(t.size, 38) * np.exp(-t * 22.0) + 0.4 * np.sin(2 * np.pi * 180 * t) * np.exp(-t * 30.0)


def _metal(sr: int, length_s: float, decay: float, seed: int) -> np.ndarray:
    t = _time(length_s, sr)
    bright = np.diff(_noise(t.size + 1, seed))  # first difference: a crude high-pass
    return 0.4 * bright * np.exp(-t * decay)


_DRUMS: dict[int, Callable[[int], np.ndarray]] = {
    36: _kick,
    38: _snare,
    42: lambda sr: _metal(sr, 0.06, 60.0, 42),
    46: lambda sr: _metal(sr, 0.35, 9.0, 46),
    49: lambda sr: 0.6 * _metal(sr, 1.2, 3.0, 49),
}


def _bass(freq: float, hold_s: float, sr: int) -> np.ndarray:
    t = _time(hold_s + 0.08, sr)
    saw = sum(np.sin(2 * np.pi * k * freq * t) / k for k in range(1, 7)) * 0.5
    warm = lfilter([0.2], [1.0, -0.8], saw)  # one-pole low-pass, unity DC gain
    return warm * _envelope(t, 0.005, hold_s, 0.08)


def _keys(freq: float, hold_s: float, sr: int) -> np.ndarray:
    t = _time(hold_s + 0.25, sr)
    tone = sum(a * np.sin(2 * np.pi * k * freq * t) for k, a in ((1, 1.0), (2, 0.3), (3, 0.1)))
    return tone * _envelope(t, 0.01, hold_s, 0.25, sustain=0.6, decay_tau_s=0.3)


def _lead(freq: float, hold_s: float, sr: int) -> np.ndarray:
    t = _time(hold_s + 0.1, sr)
    vibrato = 1.0 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * (t > 0.2)
    phase = 2 * np.pi * np.cumsum(freq * vibrato) / sr
    tone = sum(((-1) ** i) * np.sin(k * phase) / k ** 2 for i, k in enumerate((1, 3, 5, 7)))
    return tone * _envelope(t, 0.015, hold_s, 0.1)


def _voice(note: Note, hold_s: float, sr: int) -> np.ndarray:
    if note.instrument == "drums":
        return _DRUMS.get(note.pitch, _DRUMS[42])(sr)
    freq = midi_to_hz(note.pitch)
    if note.instrument == "bass":
        return _bass(freq, hold_s, sr)
    if note.instrument == "keys":
        return _keys(freq, hold_s, sr)
    return _lead(freq, hold_s, sr)


def _limit(audio: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    return audio * (PEAK_LIMIT / peak) if peak > PEAK_LIMIT else audio


def render(notes: tuple[Note, ...], tempo_bpm: float, sr: int = SR,
           offset_s: float = 0.0, total_s: float | None = None) -> np.ndarray:
    """Mono audio of `notes`; beat 0 sounds at `offset_s` seconds."""
    seconds_per_beat = 60.0 / tempo_bpm
    if total_s is None:
        last = max((offset_s + n.end * seconds_per_beat for n in notes), default=None)
        total_s = 0.0 if last is None else last + TAIL_S
    out = np.zeros(int(round(total_s * sr)))
    for note in notes:
        start = int(round((offset_s + note.start * seconds_per_beat) * sr))
        if start >= out.size:
            continue
        gain = _GAINS.get(note.instrument, _GAINS["lead"]) * note.velocity / 127.0
        wave = gain * _voice(note, note.duration * seconds_per_beat, sr)
        stop = min(out.size, start + wave.size)
        out[start:stop] += wave[: stop - start]
    return _limit(out).astype(np.float32)


def mix(*tracks: np.ndarray) -> np.ndarray:
    """Sum tracks of different lengths (zero-padded), then limit."""
    length = max((t.size for t in tracks), default=0)
    out = np.zeros(length)
    for track in tracks:
        out[: track.size] += track
    return _limit(out).astype(np.float32)
