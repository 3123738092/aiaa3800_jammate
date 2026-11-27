"""Validated one-bar rhythm exchanged by input, director and generator.

``beat_origin_s`` belongs to the input audio clock;
it is not a request to restart an already playing band. Live updates take
effect at the next unrendered bar on the output clock.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from jammate.music import MAX_TEMPO_BPM, MIN_TEMPO_BPM


PAD_DRUM_PITCHES = (36, 38, 42, 75)


class RhythmMode(str, Enum):
    FOLLOW = "follow"
    VARY = "vary"
    SIMPLIFY = "simplify"


@dataclass(frozen=True)
class RhythmContext:
    tempo_bpm: float
    onset_beats: tuple[float, ...]
    accents: tuple[float, ...]
    beat_origin_s: float = 0.0
    beats_per_bar: int = 4
    confidence: float = 1.0
    drum_pitches: tuple[tuple[int, ...], ...] = ()

    def __post_init__(self) -> None:
        # Accept JSON lists but keep the shared state immutable.
        object.__setattr__(self, "onset_beats", tuple(self.onset_beats))
        object.__setattr__(self, "accents", tuple(self.accents))
        if not math.isfinite(self.tempo_bpm) or not MIN_TEMPO_BPM <= self.tempo_bpm <= MAX_TEMPO_BPM:
            raise ValueError(f"rhythm tempo_bpm must be finite and within {MIN_TEMPO_BPM}-{MAX_TEMPO_BPM}")
        if not math.isfinite(self.beat_origin_s) or self.beat_origin_s < 0:
            raise ValueError("beat_origin_s must be finite and nonnegative")
        if type(self.beats_per_bar) is not int or not 1 <= self.beats_per_bar <= 16:
            raise ValueError("beats_per_bar must be an integer within 1..16")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be within 0-1")
        if len(self.onset_beats) != len(self.accents):
            raise ValueError("onset_beats and accents must have the same length")
        if len(self.onset_beats) > 64:
            raise ValueError("at most 64 onsets per bar are supported")
        if any(not math.isfinite(b) or not 0 <= b < self.beats_per_bar for b in self.onset_beats):
            raise ValueError(f"onset_beats must be finite and within [0, {self.beats_per_bar})")
        if any(a >= b for a, b in zip(self.onset_beats, self.onset_beats[1:])):
            raise ValueError("onset_beats must be strictly increasing without duplicates")
        if any(not math.isfinite(a) or not 0 < a <= 1 for a in self.accents):
            raise ValueError("accents must be within (0, 1]")
        pitches = tuple(tuple(group) for group in self.drum_pitches)
        if pitches and (len(pitches) != len(self.onset_beats) or any(
                not group or len(set(group)) != len(group)
                or any(type(pitch) is not int or pitch not in PAD_DRUM_PITCHES for pitch in group)
                for group in pitches)):
            raise ValueError("drum_pitches must give distinct pad sounds for each onset")
        object.__setattr__(self, "drum_pitches", pitches)
        # DSP libraries return numpy scalars, which json.dumps cannot always
        # encode. Normalize at the shared interface, including logged events.
        object.__setattr__(self, "tempo_bpm", float(self.tempo_bpm))
        object.__setattr__(self, "beat_origin_s", float(self.beat_origin_s))
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "beats_per_bar", int(self.beats_per_bar))
        object.__setattr__(self, "onset_beats", tuple(float(b) for b in self.onset_beats))
        object.__setattr__(self, "accents", tuple(float(a) for a in self.accents))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        if not self.drum_pitches:
            del data["drum_pitches"]
        return data


def load_rhythm(path: str | Path) -> RhythmContext:
    """Load the same strict schema used by ``UserRhythm`` events."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("rhythm JSON must contain an object")
        return RhythmContext(**data)
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"invalid rhythm JSON: {exc}") from exc
