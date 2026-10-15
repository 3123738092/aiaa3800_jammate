"""Key estimation with the Krumhansl-Kessler profiles."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jammate.music import Key, Note

# Krumhansl & Kessler (1982) probe-tone profiles, index 0 = tonic.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MIN_DISTINCT_PITCH_CLASSES = 3


@dataclass(frozen=True)
class KeyEstimate:
    key: Key
    score: float  # Pearson correlation with the best profile
    margin: float  # best minus runner-up; small = ambiguous (e.g. relative keys)


def pitch_class_histogram(notes: tuple[Note, ...]) -> np.ndarray:
    hist = np.zeros(12)
    for note in notes:
        hist[note.pitch % 12] += note.duration
    return hist


def estimate_key(notes: tuple[Note, ...]) -> KeyEstimate | None:
    hist = pitch_class_histogram(notes)
    if np.count_nonzero(hist) < MIN_DISTINCT_PITCH_CLASSES or np.ptp(hist) == 0:
        return None  # too little material, or a flat (e.g. chromatic) histogram
    scored = sorted(
        (
            (float(np.corrcoef(hist, np.roll(profile, tonic))[0, 1]), Key(tonic, mode))
            for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE))
            for tonic in range(12)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    (best_score, best_key), (second_score, _) = scored[0], scored[1]
    return KeyEstimate(best_key, best_score, best_score - second_score)
