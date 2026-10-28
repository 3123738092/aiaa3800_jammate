"""Hum -> notes: frame-level pitch tracking, note segmentation, quantization.

pYIN (librosa) is used instead of CREPE for the MVP: no extra model download,
and phrase-level analysis does not need CREPE's frame latency. A CREPE or
Basic Pitch backend can replace `track_pitch` behind the same signature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

import librosa
import numpy as np
from scipy.ndimage import median_filter

from jammate.music import Note

HOP_LENGTH = 256
FRAME_LENGTH = 2048
FMIN_HZ = 65.0  # C2, a low hum
FMAX_HZ = 1047.0  # C6
MIN_NOTE_S = 0.08
MAX_GAP_S = 0.05
SMOOTH_FRAMES = 5


@dataclass(frozen=True)
class TimedNote:
    pitch: int
    start_s: float
    end_s: float


def track_pitch(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (frame times s, MIDI pitch or NaN, voiced mask)."""
    f0, voiced_flag, _ = librosa.pyin(
        np.asarray(y, dtype=np.float32), fmin=FMIN_HZ, fmax=FMAX_HZ, sr=sr,
        frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH,
    )
    times = librosa.times_like(f0, sr=sr, hop_length=HOP_LENGTH)
    voiced = np.asarray(voiced_flag, dtype=bool) & np.isfinite(f0)
    midi = np.full(f0.shape, np.nan)
    midi[voiced] = librosa.hz_to_midi(f0[voiced])
    return times, midi, voiced


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True values."""
    padded = np.concatenate(([0], np.asarray(mask, dtype=int), [0]))
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _label_runs(labels: np.ndarray) -> list[list[int]]:
    """[label, start, end) for each run of equal labels."""
    runs, start = [], 0
    for i in range(1, labels.size + 1):
        if i == labels.size or labels[i] != labels[start]:
            runs.append([int(labels[start]), start, i])
            start = i
    return runs


def _absorb_short_runs(runs: list[list[int]], min_len: int) -> list[list[int]]:
    """Give glitches the pitch of a neighbour, then merge equal neighbours."""
    if len(runs) <= 1:
        return runs
    relabelled = [list(r) for r in runs]
    for i, run in enumerate(relabelled):
        if run[2] - run[1] < min_len:
            run[0] = relabelled[i - 1][0] if i > 0 else relabelled[i + 1][0]
    merged: list[list[int]] = []
    for run in relabelled:
        if merged and merged[-1][0] == run[0]:
            merged[-1][2] = run[2]
        else:
            merged.append(run)
    return merged


def _bridge_gaps(notes: list[TimedNote], max_gap: float) -> list[TimedNote]:
    bridged: list[TimedNote] = []
    for note in notes:
        prev = bridged[-1] if bridged else None
        if prev and prev.pitch == note.pitch and note.start_s - prev.end_s <= max_gap + 1e-9:
            bridged[-1] = TimedNote(note.pitch, prev.start_s, note.end_s)
        else:
            bridged.append(note)
    return bridged


def segment_notes(
    times: np.ndarray, midi: np.ndarray, voiced: np.ndarray,
    min_duration: float = MIN_NOTE_S, max_gap: float = MAX_GAP_S,
) -> tuple[TimedNote, ...]:
    times = np.asarray(times, dtype=float)
    midi = np.asarray(midi, dtype=float)
    if times.size < 2:
        return ()
    frame = float(np.median(np.diff(times)))
    min_frames = max(1, math.ceil(min_duration / frame - 1e-6))
    notes = []
    for a, b in _runs(np.asarray(voiced, dtype=bool)):
        segment = midi[a:b]
        if segment.size >= SMOOTH_FRAMES:
            segment = median_filter(segment, size=SMOOTH_FRAMES, mode="nearest")
        for pitch, s, e in _absorb_short_runs(_label_runs(np.round(segment).astype(int)), min_frames):
            notes.append(TimedNote(pitch, float(times[a + s]), float(times[a + e - 1] + frame)))
    bridged = _bridge_gaps(notes, max_gap)
    return tuple(n for n in bridged if n.end_s - n.start_s >= min_duration - 1e-6)


def to_beats(
    notes: Sequence[TimedNote], tempo_bpm: float, origin_s: float, grid: float = 0.25
) -> tuple[Note, ...]:
    """Quantize to a beat grid (default sixteenth notes); drop notes before origin."""
    seconds_per_beat = 60.0 / tempo_bpm

    def quantize(seconds: float) -> float:
        return round((seconds - origin_s) / seconds_per_beat / grid) * grid

    placed: list[Note] = []
    for note in sorted(notes, key=lambda n: n.start_s):
        start = quantize(note.start_s)
        if start < 0:
            continue
        end = max(quantize(note.end_s), start + grid)
        if placed and placed[-1].end > start:
            if start <= placed[-1].start:
                continue  # two notes landed on one grid slot: keep the first
            placed[-1] = replace(placed[-1], duration=start - placed[-1].start)
        placed.append(Note(note.pitch, start, end - start, instrument="user"))
    return tuple(placed)
