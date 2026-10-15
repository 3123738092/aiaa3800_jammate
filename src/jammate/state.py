"""Shared jam state: the single record the band director reads and replaces."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from jammate.music import Key, Note

MIN_TEMPO_BPM = 60.0
MAX_TEMPO_BPM = 180.0
MAX_ENERGY = 3


class Action(str, Enum):
    FOLLOW = "follow"  # answer the user's phrase
    VARY = "vary"  # change harmony / re-spin the last idea
    BUILD = "build"  # raise intensity
    BREAK = "break"  # drop to a sparse texture
    INITIATE = "initiate"  # AI throws a new motif to the user
    ADJUST = "adjust"  # tempo/energy tweak without new material
    HOLD = "hold"  # keep the groove as is
    STOP = "stop"


@dataclass(frozen=True)
class JamState:
    tempo_bpm: float = 96.0
    key: Key = Key(0, "major")
    energy: int = 1
    progression_variant: int = 0
    last_user_motif: tuple[Note, ...] = ()
    last_ai_motif: tuple[Note, ...] = ()
    playing: bool = True

    def __post_init__(self) -> None:
        if not MIN_TEMPO_BPM <= self.tempo_bpm <= MAX_TEMPO_BPM:
            raise ValueError(
                f"tempo must be within {MIN_TEMPO_BPM}-{MAX_TEMPO_BPM} BPM, got {self.tempo_bpm}"
            )
        if not 0 <= self.energy <= MAX_ENERGY:
            raise ValueError(f"energy must be within 0-{MAX_ENERGY}, got {self.energy}")

    def with_tempo(self, bpm: float) -> JamState:
        return replace(self, tempo_bpm=min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, bpm)))

    def with_energy(self, level: int) -> JamState:
        return replace(self, energy=min(MAX_ENERGY, max(0, level)))

    def with_updates(self, **changes: object) -> JamState:
        return replace(self, **changes)
