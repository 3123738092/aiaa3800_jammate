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
                       confidence: float = 1.0, beats_per_bar: int = 4) -> RhythmContext:
    """Quantize one explicit bar onto a sixteenth-note grid.

Times are absolute seconds on the input clock; onsets outside this bar are
ignored. Multiple detections in one grid cell keep their strongest accent.
"""
    RhythmContext(tempo_bpm, (), (), beat_origin_s, beats_per_bar=beats_per_bar, confidence=confidence)
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
        if not 0 <= beat < beats_per_bar:
            continue
        snapped = round(beat * 4) / 4
        if snapped >= beats_per_bar:
            continue  # a late event must not wrap around to the beginning
        cells[snapped] = max(cells.get(snapped, 0), accent)
        errors.append(abs(beat - snapped))
    fit = max(0.0, 1.0 - float(np.mean(errors)) / 0.25) if errors else 0.0
    beats = tuple(sorted(cells))
    return RhythmContext(tempo_bpm, beats, tuple(cells[b] for b in beats),
                         beat_origin_s, beats_per_bar=beats_per_bar, confidence=confidence * fit)


def _onsets_and_accents(y: np.ndarray, sr: int, *, backtrack: bool = True,
                        relative_peak_floor: float = .12,
                        absolute_peak_floor: float = .01) -> tuple[np.ndarray, np.ndarray]:
    audio = np.asarray(y, dtype=np.float32)
    if audio.ndim != 1 or not np.isfinite(audio).all() or sr <= 0:
        raise ValueError("rhythm audio must be finite mono audio with a positive sample rate")
    max_peak = float(np.max(np.abs(audio), initial=0.0))
    if max_peak < absolute_peak_floor:
        return np.array([]), np.array([])
    # Padding allows a clap at sample zero to be detected as an onset.
    pad = int(round(0.1 * sr))
    padded = np.pad(audio, (pad, pad))
    onsets = librosa.onset.onset_detect(y=padded, sr=sr, hop_length=ONSET_HOP,
                                       units="time", backtrack=backtrack) - pad / sr
    # Backtracking may land up to one analysis frame before the attack.
    onsets = np.maximum(0, onsets)
    onsets = onsets[onsets < audio.size / sr]
    peaks = np.array([np.max(np.abs(audio[int(t * sr):min(audio.size, int(t * sr) + max(1, int(.06 * sr)))]))
                      for t in onsets])
    keep = peaks >= max(absolute_peak_floor, max_peak * relative_peak_floor)
    peaks = peaks[keep]
    return onsets[keep], peaks / peaks.max() if peaks.size else peaks


def analyze_rhythm_audio(y: np.ndarray, sr: int, tempo_hint: float | None = None,
                         count_in: int = 4, beat_origin_s: float | None = None,
                         beats_per_bar: int = 4) -> RhythmContext:
    """Read steady count-in claps, then the first full bar of a rhythm recording.

With count_in=0, tempo_hint is required and beat zero defaults to the first
detected attack. Pass beat_origin_s explicitly to preserve leading rests.
An incomplete bar is rejected rather than inventing its missing contents.
"""
    if not isinstance(count_in, int) or count_in < 0 or count_in in (1, 2):
        raise ValueError("count_in must be 0 or at least 3")
    onsets, accents = _onsets_and_accents(y, sr)
    if count_in:
        tempo = tempo_from_onsets(onsets[:count_in]) if len(onsets) >= count_in else None
        if tempo is None:
            # On softer syllables, backtracking can place the candidate before
            # its audible attack. Retry at the detected position with a lower
            # relative peak floor, still requiring a steady count-in.
            soft_onsets, soft_accents = _onsets_and_accents(
                y, sr, backtrack=False, relative_peak_floor=.075, absolute_peak_floor=.008,
            )
            if len(soft_onsets) >= count_in:
                tempo = tempo_from_onsets(soft_onsets[:count_in])
                if tempo is not None:
                    onsets, accents = soft_onsets, soft_accents
        if len(onsets) < count_in:
            raise ValueError("not enough count-in claps; use --count-in 0 with --tempo")
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
    RhythmContext(tempo, (), (), origin, beats_per_bar=beats_per_bar)
    # One hop of tolerance covers count-in timestamp quantization.
    if len(y) / sr + ONSET_HOP / sr < origin + beats_per_bar * 60.0 / tempo:
        raise ValueError("recording must contain a complete rhythm bar after the count-in/origin")
    # Backtracked attacks can be a frame early at the downbeat.
    near_origin = np.abs(onsets - origin) <= ONSET_HOP / sr
    onsets[near_origin] = origin
    return rhythm_from_onsets(onsets, tempo, origin, accents, beats_per_bar=beats_per_bar)


def analyze_rhythm_bar(y: np.ndarray, sr: int, tempo_bpm: float,
                       beat_origin_s: float = 0.0, beats_per_bar: int = 4) -> RhythmContext:
    """Analyze exactly one bar captured on an existing playback grid."""
    onsets, accents = _onsets_and_accents(y, sr)
    return rhythm_from_onsets(onsets + beat_origin_s, tempo_bpm, beat_origin_s, accents,
                              beats_per_bar=beats_per_bar)


def analyze_rhythm_feel(y: np.ndarray, sr: int, tempo_bpm: float, beats_per_bar: int = 4) -> RhythmContext:
    """Quantize a short rhythm sketch to the supplied session tempo."""
    onsets, accents = _onsets_and_accents(y, sr)
    if len(onsets) < 2:
        raise ValueError("至少需要两个清晰的节奏起音。")
    result = rhythm_from_onsets(onsets, tempo_bpm, float(onsets[0]), accents, beats_per_bar=beats_per_bar)
    if len(result.onset_beats) < 2:
        raise ValueError("当前速度的一个小节里没有足够的节奏起音。")
    return result
