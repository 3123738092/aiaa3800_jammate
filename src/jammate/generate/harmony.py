"""Diatonic triads and a small set of loop-friendly progressions."""

from __future__ import annotations

from jammate.music import Chord, Key

_QUALITY_BY_INTERVALS = {(4, 7): "maj", (3, 7): "min", (3, 6): "dim"}

# Scale-degree loops (0 = tonic). Short, repeating loops suit jamming because
# the user can predict where the harmony goes.
PROGRESSIONS = {
    "major": ((0, 4, 5, 3), (0, 3, 4, 4), (5, 3, 0, 4)),  # I-V-vi-IV, I-IV-V-V, vi-IV-I-V
    "minor": ((0, 5, 2, 6), (0, 3, 4, 0), (0, 6, 5, 4)),  # i-VI-III-VII, i-iv-v-i, i-VII-VI-v
}


def triad(key: Key, degree: int) -> Chord:
    root = key.from_index(degree)
    third = key.from_index(degree + 2) - root
    fifth = key.from_index(degree + 4) - root
    return Chord(root % 12, _QUALITY_BY_INTERVALS[(third, fifth)])


def progression(key: Key, variant: int = 0) -> tuple[Chord, ...]:
    loops = PROGRESSIONS[key.mode]
    degrees = loops[variant % len(loops)]
    return tuple(triad(key, d) for d in degrees)
