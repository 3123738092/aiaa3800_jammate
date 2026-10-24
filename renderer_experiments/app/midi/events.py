from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EventType = Literal["note_on", "note_off", "control_change", "all_notes_off"]

@dataclass(frozen=True)
class MidiEvent:
    timestamp: float
    type: EventType
    channel: int = 0
    note: int | None = None
    velocity: int = 0
    control: int | None = None
    value: int | None = None


def clamp_midi(value: float) -> int:
    return max(1, min(127, int(round(value))))
