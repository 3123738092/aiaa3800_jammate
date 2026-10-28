from __future__ import annotations

from dataclasses import replace
from typing import Any

from .events import MidiEvent, clamp_midi


def apply_render_overrides(events: list[MidiEvent], overrides: dict[str, Any]) -> list[MidiEvent]:
    bass_scale = float(overrides.get("bass_velocity_scale", 1.0))
    reverb = {int(channel): int(value) for channel, value in overrides.get("cc91_reverb", {}).items()}
    result: list[MidiEvent] = []
    for event in events:
        if event.type == "note_on" and event.channel == 2:
            result.append(replace(event, velocity=clamp_midi(event.velocity * bass_scale)))
        elif event.type == "control_change" and event.control == 91 and event.channel in reverb:
            result.append(replace(event, value=reverb[event.channel]))
        else:
            result.append(event)
    return result
