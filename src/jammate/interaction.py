"""Renderer-independent interaction vocabulary and immutable state sections."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from jammate.music import Note
from jammate.rhythm import RhythmContext


class Strategy(str, Enum):
    LISTEN = "LISTEN"
    FOLLOW = "FOLLOW"
    RESPOND = "RESPOND"
    INITIATE = "INITIATE"


class JamAction(str, Enum):
    PRESERVE = "PRESERVE"
    ECHO_MOTIF = "ECHO_MOTIF"
    VARY_MOTIF = "VARY_MOTIF"
    INTRODUCE_MOTIF = "INTRODUCE_MOTIF"
    BUILD = "BUILD"
    SIMPLIFY = "SIMPLIFY"
    ADJUST_FEEL = "ADJUST_FEEL"
    ADD_LAYER = "ADD_LAYER"
    REMOVE_LAYER = "REMOVE_LAYER"
    FILL = "FILL"


def unit(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite within 0..1")


@dataclass(frozen=True)
class TransportPosition:
    bar: int = 0
    beat: float = 0.0
    next_bar: int = 0  # first bar not yet rendered; published by Runtime

    def __post_init__(self):
        if type(self.bar) is not int or self.bar < 0 or type(self.next_bar) is not int or self.next_bar < self.bar:
            raise ValueError("transport bars must be nonnegative integers with next_bar >= bar")
        if isinstance(self.beat, bool) or not math.isfinite(self.beat) or not 0 <= self.beat < 4:
            raise ValueError("beat must be within [0,4)")


@dataclass(frozen=True)
class Layer:
    name: str
    active: bool = True
    level: float = 0.5
    density: float = 1.0
    role: str = "support"

    def __post_init__(self):
        if self.name not in {"drums", "bass", "keys", "lead"} or type(self.active) is not bool:
            raise ValueError("invalid layer name/active flag")
        unit(self.level, "layer level")
        unit(self.density, "layer density")


@dataclass(frozen=True)
class Motif:
    motif_id: str
    owner: str
    source_event_ids: tuple[str, ...]
    notes: tuple[Note, ...]

    def __post_init__(self):
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids))
        object.__setattr__(self, "notes", tuple(self.notes))
        if not self.motif_id or self.owner not in {"user", "ai"} or not self.notes:
            raise ValueError("motif needs id, owner and notes")
        for note in self.notes:
            if type(note.pitch) is not int or not 0 <= note.pitch <= 127 or not math.isfinite(note.start) or note.start < 0 or not math.isfinite(note.duration) or note.duration <= 0:
                raise ValueError("invalid motif note")


@dataclass(frozen=True)
class RhythmSource:
    event_id: str
    context: RhythmContext


@dataclass(frozen=True)
class Interaction:
    strategy: Strategy = Strategy.FOLLOW
    initiative: float = 0.0
    current_lead: str | None = "user"
    user_active: bool = False
    awaiting_user: bool = False
    last_feedback: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "strategy", Strategy(self.strategy))
        unit(self.initiative, "initiative")
        if self.current_lead not in {"user", "ai", None} or type(self.user_active) is not bool or type(self.awaiting_user) is not bool:
            raise ValueError("invalid interaction state")
