"""Extract one bar of rhythm without depending on a pitched/voiced signal.

Count-in claps must be a steady pulse. Syncopated playing uses the measured
count-in tempo or an explicit tempo hint, never median-IOI tempo estimation.
Confidence is a grid-fit heuristic, not a calibrated probability.
"""

from __future__ import annotations

import math
from typing import Sequence

import librosa
import numpy as np

from jammate.analysis.tempo import ONSET_HOP, tempo_from_onsets
from jammate.rhythm import RhythmContext


def rhythm_from_onsets(onset_times: Sequence[float], tempo_bpm: float,
                       beat_origin_s: float, accents: Sequence[float] | None = None,
                       confidence: float = 1.0) -> RhythmContext:
    """Quantize one explicit 4/4 bar onto a sixteenth-note grid.

Times are absolute seconds on the input clock; onsets outside this bar are
ignored. Multiple detections in one grid cell keep their strongest accent.
"""
    RhythmContext(tempo_bpm, (), (), beat_origin_s, confidence=confidence)
    times = tuple(onset_times)
    strengths = tuple(accents) if accents is not None else (1.0,) * len(times)
    if len(strengths) != len(times):
        raise ValueError("onset times and accents must have the same length")
    cells: dict[float, float] = {}
    errors = []
    for time_s, accent in zip(times, strengths):
        if not math.isfinite(time_s) or not math.isfinite(accent) or not 0 < accent <= 1:
            raise ValueError("onset times must be finite; accents must be within (0, 1]")
        beat = (time_s - beat_origin_s) * tempo_bpm / 60.0
        if not 0 <= beat < 4:
            continue
        snapped = round(beat * 4) / 4
        if snapped >= 4:
            continue  # a late event must not wrap around to the beginning
        cells[snapped] = max(cells.get(snapped, 0), accent)
        errors.append(abs(beat - snapped))
    fit = max(0.0, 1.0 - float(np.mean(errors)) / 0.25) if errors else 0.0
    beats = tuple(sorted(cells))
    return RhythmContext(tempo_bpm, beats, tuple(cells[b] for b in beats),
                         beat_origin_s, confidence=confidence * fit)


def _onsets_and_accents(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    audio = np.asarray(y, dtype=np.float32)
    if audio.ndim != 1 or not np.isfinite(audio).all() or sr <= 0:
        raise ValueError("rhythm audio must be finite mono audio with a positive sample rate")
    if not audio.size or np.max(np.abs(audio)) < 0.01:
        return np.array([]), np.array([])
    # Padding allows a clap at sample zero to be detected as an onset.
    pad = int(round(0.1 * sr))
    padded = np.pad(audio, (pad, pad))
    onsets = librosa.onset.onset_detect(y=padded, sr=sr, hop_length=ONSET_HOP,
                                       units="time", backtrack=True) - pad / sr
    # Backtracking may land up to one analysis frame before the attack.
    onsets = np.maximum(0, onsets)
    onsets = onsets[onsets < audio.size / sr]
    peaks = np.array([np.max(np.abs(audio[int(t * sr):min(audio.size, int(t * sr) + max(1, int(.06 * sr)))]))
                      for t in onsets])
    keep = peaks >= max(0.01, float(np.max(np.abs(audio))) * 0.12)
    peaks = peaks[keep]
    return onsets[keep], peaks / peaks.max() if peaks.size else peaks


def analyze_rhythm_audio(y: np.ndarray, sr: int, tempo_hint: float | None = None,
                         count_in: int = 4, beat_origin_s: float | None = None) -> RhythmContext:
    """Read steady count-in claps, then the first full bar of a rhythm recording.

With count_in=0, tempo_hint is required and beat zero defaults to the first
detected attack. Pass beat_origin_s explicitly to preserve leading rests.
An incomplete bar is rejected rather than inventing its missing contents.
"""
    if not isinstance(count_in, int) or count_in < 0 or count_in in (1, 2):
        raise ValueError("count_in must be 0 or at least 3")
    onsets, accents = _onsets_and_accents(y, sr)
    if count_in:
        if len(onsets) < count_in:
            raise ValueError("not enough count-in claps; use --count-in 0 with --tempo")
        tempo = tempo_from_onsets(onsets[:count_in])
        if tempo is None:
            raise ValueError("count-in is not steady; use --count-in 0 with --tempo")
        origin = float(onsets[count_in - 1] + 60.0 / tempo)
        # Isolate the rhythm from count-in attacks near the selected origin.
        onsets, accents = onsets[count_in:], accents[count_in:]
    else:
        if tempo_hint is None:
            raise ValueError("tempo_hint is required when count_in=0")
        tempo = tempo_hint
        origin = float(onsets[0]) if onsets.size else 0.0
    if beat_origin_s is not None:
        origin = beat_origin_s
    RhythmContext(tempo, (), (), origin)
    # One hop of tolerance covers count-in timestamp quantization.
    if len(y) / sr + ONSET_HOP / sr < origin + 4 * 60.0 / tempo:
        raise ValueError("recording must contain a complete rhythm bar after the count-in/origin")
    # Backtracked attacks can be a frame early at the downbeat.
    near_origin = np.abs(onsets - origin) <= ONSET_HOP / sr
    onsets[near_origin] = origin
    return rhythm_from_onsets(onsets, tempo, origin, accents)


def analyze_rhythm_bar(y: np.ndarray, sr: int, tempo_bpm: float,
                       beat_origin_s: float = 0.0) -> RhythmContext:
    """Analyze exactly one bar captured on an existing playback grid."""
    onsets, accents = _onsets_and_accents(y, sr)
    return rhythm_from_onsets(onsets + beat_origin_s, tempo_bpm, beat_origin_s, accents)
