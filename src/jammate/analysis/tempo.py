"""Tempo from a short count-in of claps or snaps."""

from __future__ import annotations

from typing import Sequence

import librosa
import numpy as np

MIN_BPM, MAX_BPM = 60.0, 180.0
MIN_IOI_S = 0.15  # anything closer is one clap detected twice
MAX_IOI_CV = 0.2  # claps must be steady to be read as a count-in
ONSET_HOP = 256


def detect_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    """Onset times in seconds (claps, snaps, sung attacks)."""
    return librosa.onset.onset_detect(
        y=np.asarray(y, dtype=np.float32), sr=sr, hop_length=ONSET_HOP, units="time"
    )


def tempo_from_onsets(
    onset_times: Sequence[float], min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM
) -> float | None:
    """BPM from the median inter-onset interval, or None if not a steady pulse."""
    if not (0 < min_bpm and max_bpm >= 2 * min_bpm):
        # Octave folding needs a range at least one octave wide, or it never terminates.
        raise ValueError(f"need 0 < min_bpm and max_bpm >= 2 * min_bpm, got {min_bpm}-{max_bpm}")
    times = np.sort(np.asarray(onset_times, dtype=float))
    if times.size < 3:
        return None
    iois = np.diff(times)
    iois = iois[iois >= MIN_IOI_S]
    if iois.size < 2 or np.std(iois) / np.mean(iois) > MAX_IOI_CV:
        return None
    bpm = 60.0 / float(np.median(iois))
    while bpm < min_bpm:
        bpm *= 2.0
    while bpm > max_bpm:
        bpm /= 2.0
    return bpm
