"""Core music primitives: notes, keys, chords and pitch conversions.

Time is measured in beats throughout the package; only the analysis and
render layers convert to seconds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SCALE_STEPS = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),  # natural minor
}
CHORD_INTERVALS = {"maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6)}
CHORD_SUFFIX = {"maj": "", "min": "m", "dim": "dim"}
BEATS_PER_BAR = 4


@dataclass(frozen=True)
class Note:
    """One note event. `start`/`duration` are in beats."""

    pitch: int
    start: float
    duration: float
    velocity: int = 90
    instrument: str = "lead"

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError(f"note duration must be positive, got {self.duration}")
        if not 0 <= self.velocity <= 127:
            raise ValueError(f"velocity must be in 0..127, got {self.velocity}")

    @property
    def end(self) -> float:
        return self.start + self.duration

    def shifted(self, beats: float) -> Note:
        return replace(self, start=self.start + beats)


@dataclass(frozen=True)
class Key:
    """A diatonic key. `tonic` is a pitch class (0 = C)."""

    tonic: int
    mode: str = "major"

    def __post_init__(self) -> None:
        if not 0 <= self.tonic <= 11:
            raise ValueError(f"tonic must be a pitch class 0..11, got {self.tonic}")
        if self.mode not in SCALE_STEPS:
            raise ValueError(f"unsupported mode {self.mode!r}")

    @property
    def steps(self) -> tuple[int, ...]:
        return SCALE_STEPS[self.mode]

    @property
    def scale_pcs(self) -> tuple[int, ...]:
        return tuple((self.tonic + s) % 12 for s in self.steps)

    @property
    def name(self) -> str:
        return f"{PITCH_CLASS_NAMES[self.tonic]} {self.mode}"

    def contains(self, pitch: int) -> bool:
        return pitch % 12 in self.scale_pcs

    def snap(self, pitch: int) -> int:
        """Nearest scale tone; ties go down so results are deterministic."""
        for distance in range(7):
            for candidate in (pitch - distance, pitch + distance):
                if self.contains(candidate):
                    return candidate
        raise AssertionError("every pitch is within a fifth of a scale tone")

    def to_index(self, pitch: int) -> int:
        """Scale-degree index counted across octaves (snaps off-scale pitches)."""
        octave, remainder = divmod(self.snap(pitch) - self.tonic, 12)
        return octave * len(self.steps) + self.steps.index(remainder)

    def from_index(self, index: int) -> int:
        octave, degree = divmod(index, len(self.steps))
        return self.tonic + 12 * octave + self.steps[degree]

    def transpose_diatonic(self, pitch: int, steps: int) -> int:
        return self.from_index(self.to_index(pitch) + steps)


@dataclass(frozen=True)
class Chord:
    root: int
    quality: str = "maj"

    def __post_init__(self) -> None:
        if self.quality not in CHORD_INTERVALS:
            raise ValueError(f"unsupported chord quality {self.quality!r}")

    @property
    def pcs(self) -> tuple[int, ...]:
        return tuple((self.root + i) % 12 for i in CHORD_INTERVALS[self.quality])

    @property
    def name(self) -> str:
        return PITCH_CLASS_NAMES[self.root] + CHORD_SUFFIX[self.quality]

    def voicing(self, low: int) -> tuple[int, ...]:
        """Root-position voicing whose root is the lowest pitch >= `low`."""
        base = low + (self.root - low) % 12
        return tuple(base + i for i in CHORD_INTERVALS[self.quality])


def hz_to_midi(hz: float) -> float:
    if hz <= 0:
        return math.nan
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
