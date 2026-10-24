from __future__ import annotations

import re

from ..models import MusicIntent
from .events import MidiEvent, clamp_midi

DRUMS = {"kick": 36, "snare": 38, "closed_hat": 42, "open_hat": 46, "ride": 51, "crash": 49}
ROOTS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
QUALITIES = {"maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "7": (0, 4, 7, 10), "maj": (0, 4, 7), "m": (0, 3, 7), "": (0, 4, 7)}


def _pair(t: float, note: int, velocity: int, duration: float, channel: int) -> list[MidiEvent]:
    return [MidiEvent(t, "note_on", channel, note, velocity), MidiEvent(t + duration, "note_off", channel, note, 0)]


def parse_chord(symbol: str) -> tuple[str, tuple[int, ...]]:
    match = re.fullmatch(r"([A-G](?:#|b)?)(maj7|m7|maj|m|7)?", symbol)
    if not match:
        raise ValueError(f"unsupported chord symbol: {symbol}")
    root, quality = match.groups()
    return root, QUALITIES[quality or ""]


def chord_notes(symbol: str, base: int = 60) -> tuple[int, ...]:
    root, intervals = parse_chord(symbol)
    return tuple(base + ROOTS[root] + interval for interval in intervals)


def _style(intent: MusicIntent) -> str:
    return intent.semantic.style[0].lower() if intent.semantic.style else "default"


def drum_pattern(intent: MusicIntent, bars: int = 1) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    style = _style(intent)
    velocity = clamp_midi(58 + 57 * intent.semantic.energy)
    patterns = {
        "funk": {"kick": (0, 1.75, 2.5, 3.25), "snare": (1, 3), "hat": tuple(i * .5 for i in range(8))},
        "indie rock": {"kick": (0, 2, 2.75), "snare": (1, 3), "hat": tuple(i * .5 for i in range(8))},
        "lo-fi hip hop": {"kick": (0, 1.5, 2.75), "snare": (1.0, 3.0), "hat": (0, .5, 1.5, 2, 2.5, 3.5)},
        "jazz fusion": {"kick": (0, 1.5, 2.75), "snare": (1.25, 3.25), "hat": tuple(i * .5 for i in range(8))},
        "ambient": {"kick": (0,), "snare": (), "hat": (1.5, 3.5)},
    }
    pattern = patterns.get(style, patterns["indie rock"])
    events: list[MidiEvent] = []
    for bar in range(bars):
        start = bar * 4 * beat
        for position in pattern["kick"]:
            events += _pair(start + position * beat, DRUMS["kick"], velocity, .11 * beat, 9)
        for position in pattern["snare"]:
            events += _pair(start + position * beat, DRUMS["snare"], velocity - 8, .11 * beat, 9)
        for index, position in enumerate(pattern["hat"]):
            note = DRUMS["ride"] if style == "jazz fusion" else DRUMS["closed_hat"]
            hat_velocity = velocity - 28 + (8 if index % 2 == 0 else 0)
            events += _pair(start + position * beat, note, max(1, hat_velocity), .08 * beat, 9)
        if style == "indie rock" and bar % 4 == 3:
            events += _pair(start + 3.5 * beat, DRUMS["crash"], velocity, .22 * beat, 9)
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))


def bass_pattern(intent: MusicIntent, bars: int = 1) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    style = _style(intent)
    lines = {
        "funk": ((0, 0, 0, .42), (1.5, 2, 0, .24), (2, 0, 12, .34), (2.75, 1, 0, .22), (3.5, 2, 0, .2)),
        "indie rock": ((0, 0, 0, .72), (1, 0, 0, .38), (2, 2, 0, .64), (3, 0, 0, .42)),
        "lo-fi hip hop": ((0, 0, 0, 1.35), (2, 2, 0, .62), (3.5, 1, 0, .27)),
        "jazz fusion": ((0, 0, 0, .48), (.75, 1, 0, .2), (1.5, 2, 0, .34), (2.5, 1, 0, .2), (3, 2, 12, .3), (3.5, 3, 0, .2)),
        "ambient": ((0, 0, 0, 2.7), (2.5, 2, 0, 1.15)),
    }
    line = lines.get(style, lines["indie rock"])
    velocity = clamp_midi(42 + 60 * intent.semantic.energy)
    events: list[MidiEvent] = []
    for bar in range(bars):
        notes = chord_notes(intent.harmony.chords[bar % len(intent.harmony.chords)], 36)
        for position, tone, octave, length in line:
            note = notes[min(tone, len(notes) - 1)] + octave
            events += _pair(bar * 4 * beat + position * beat, note, velocity, length * beat, 2)
        if style in {"funk", "jazz fusion"} and bar % 2 == 1:
            next_notes = chord_notes(intent.harmony.chords[(bar + 1) % len(intent.harmony.chords)], 36)
            approach = next_notes[0] - 1
            events += _pair((bar + 1) * 4 * beat - .22 * beat, approach, velocity - 14, .16 * beat, 2)
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))


def harmony_pattern(intent: MusicIntent, bars: int = 1) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    style = _style(intent)
    events: list[MidiEvent] = []
    velocity = clamp_midi(38 + 54 * intent.semantic.energy)
    for bar in range(bars):
        notes = chord_notes(intent.harmony.chords[bar % len(intent.harmony.chords)], 60)
        if style == "funk":
            positions = ((.5, .36), (1.75, .28), (3.0, .36))
        elif style == "jazz fusion":
            positions = ((0, .42), (1.5, .32), (2.75, .42))
        elif style == "ambient":
            positions = ((0, 3.65),)
        else:
            positions = ((0, 1.7), (2, 1.45))
        voicing = notes[1:] + (notes[0] + 12,)
        for position, length in positions:
            for note in voicing:
                events += _pair(bar * 4 * beat + position * beat, note, velocity, length * beat, 1)
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))




def _performance_pair(t: float, note: int, velocity: int, duration: float, channel: int, index: int) -> list[MidiEvent]:
    """Apply a tiny deterministic offset so a phrase is not perfectly quantized."""
    offsets = (-0.014, -0.007, 0.0, 0.006, 0.012)
    shifted = max(0.0, t + offsets[index % len(offsets)])
    return _pair(shifted, note, velocity, duration, channel)


def _cc(timestamp: float, channel: int, control: int, value: int) -> MidiEvent:
    return MidiEvent(timestamp, "control_change", channel=channel, control=control, value=value)


def _performance_drums(intent: MusicIntent, bars: int) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    base_velocity = clamp_midi(64 + 48 * intent.semantic.energy)
    events: list[MidiEvent] = []
    kick_variants = (
        (0, 1.75, 2.5, 3.25),
        (0, .75, 2.0, 2.75, 3.5),
        (0, 1.5, 2.25, 3.0),
        (0, .75, 2.5, 3.25),
    )
    for bar in range(bars):
        start = bar * 4 * beat
        phrase = bar % 4
        if bar in (0, 4):
            events += _performance_pair(start, DRUMS["crash"], base_velocity + 8, .22 * beat, 9, bar)
        for index, pos in enumerate(kick_variants[phrase]):
            events += _performance_pair(start + pos * beat, DRUMS["kick"], base_velocity + (8 if pos in (0, 2) else 0), .10 * beat, 9, index + bar)
        for index, pos in enumerate((1, 3)):
            events += _performance_pair(start + pos * beat, DRUMS["snare"], base_velocity - 3, .10 * beat, 9, index + 2 * bar)
        # Sixteenth-note hats with accents and slight articulation variation.
        for step in range(16):
            pos = step * .25
            if phrase == 3 and step >= 12:
                continue
            accent = 13 if step % 4 == 0 else 4 if step % 2 == 0 else -10
            events += _performance_pair(start + pos * beat, DRUMS["closed_hat"], max(24, base_velocity - 26 + accent), .055 * beat, 9, step + bar)
        events += _performance_pair(start + 3.75 * beat, DRUMS["open_hat"], base_velocity - 18, .16 * beat, 9, bar)
        # Ghost notes and two-bar phrase fills make the groove breathe.
        if phrase in (1, 3):
            events += _performance_pair(start + 1.75 * beat, DRUMS["snare"], max(18, base_velocity - 42), .055 * beat, 9, bar)
        if phrase == 3:
            for index, (pos, note) in enumerate(((3.0, 45), (3.25, 47), (3.5, 50), (3.75, DRUMS["snare"]))):
                events += _performance_pair(start + pos * beat, note, base_velocity - index * 4, .09 * beat, 9, bar + index)
    return events


def _performance_bass(intent: MusicIntent, bars: int) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    velocity = clamp_midi(48 + 58 * intent.semantic.energy)
    events: list[MidiEvent] = []
    phrases = (
        ((0.0, 0, 0, .55), (.75, 2, 0, .20), (1.5, 0, 12, .30), (2.5, 1, 0, .20), (3.25, 2, 0, .24)),
        ((0.0, 0, 0, .42), (1.0, 1, 0, .18), (1.75, 2, 0, .26), (2.75, 0, 12, .30), (3.5, 1, 0, .18)),
        ((0.0, 0, 0, .66), (1.5, 2, 0, .22), (2.0, 0, 12, .30), (3.0, 2, 0, .20)),
        ((0.0, 0, 0, .45), (.75, 1, 0, .18), (1.5, 2, 0, .22), (2.5, 1, 0, .18), (3.0, 0, 12, .25)),
    )
    for bar in range(bars):
        notes = chord_notes(intent.harmony.chords[bar % len(intent.harmony.chords)], 36)
        start = bar * 4 * beat
        for index, (pos, tone, octave, length) in enumerate(phrases[bar % 4]):
            note = notes[min(tone, len(notes) - 1)] + octave
            vel = velocity + (7 if index == 0 else -5 if index % 2 else 0)
            events += _performance_pair(start + pos * beat, note, clamp_midi(vel), length * beat, 2, index + bar)
        # Chromatic approach into the next bar, except at the final cadence.
        if bar < bars - 1:
            next_root = chord_notes(intent.harmony.chords[(bar + 1) % len(intent.harmony.chords)], 36)[0]
            events += _performance_pair(start + 3.78 * beat, next_root - 1, max(25, velocity - 18), .14 * beat, 2, bar)
    return events


def _nearest_voicing(notes: tuple[int, ...], previous: tuple[int, ...] | None) -> tuple[int, ...]:
    candidates: list[tuple[int, ...]] = []
    for inversion in range(len(notes)):
        candidate = notes[inversion:] + tuple(note + 12 for note in notes[:inversion])
        candidates.append(candidate)
        candidates.append(tuple(note - 12 for note in candidate))
    if previous is None:
        return min(candidates, key=lambda candidate: abs(sum(candidate) / len(candidate) - 66))
    return min(candidates, key=lambda candidate: sum(abs(a - b) for a, b in zip(candidate, previous)))


def _performance_harmony(intent: MusicIntent, bars: int) -> list[MidiEvent]:
    beat = 60 / intent.tempo.bpm
    velocity = clamp_midi(44 + 50 * intent.semantic.energy)
    events: list[MidiEvent] = []
    previous: tuple[int, ...] | None = None
    rhythms = (
        ((.5, .30), (1.75, .22), (2.75, .30)),
        ((.25, .25), (1.5, .32), (3.0, .22)),
        ((.5, .36), (2.0, .20), (3.25, .28)),
        ((.25, .22), (1.25, .30), (2.75, .22)),
    )
    for bar in range(bars):
        start = bar * 4 * beat
        raw = chord_notes(intent.harmony.chords[bar % len(intent.harmony.chords)], 60)
        voicing = _nearest_voicing(raw, previous)
        previous = voicing
        for index, (pos, length) in enumerate(rhythms[bar % 4]):
            for note_index, note in enumerate(voicing[1:] + (voicing[0] + 12,)):
                vel = velocity + (5 if note_index == len(voicing) - 1 else 0) - index * 3
                events += _performance_pair(start + pos * beat, note, clamp_midi(vel), length * beat, 1, bar + index + note_index)
    return events


def _performance_ornament(intent: MusicIntent, bars: int) -> list[MidiEvent]:
    """Clavinet call-and-response; channel 3 uses an optional program profile."""
    beat = 60 / intent.tempo.bpm
    events: list[MidiEvent] = []
    for bar in range(bars):
        if bar % 2 == 0:
            continue
        notes = chord_notes(intent.harmony.chords[bar % len(intent.harmony.chords)], 72)
        start = bar * 4 * beat
        for index, pos in enumerate((.75, 1.25, 2.75, 3.25)):
            note = notes[(index + 1) % len(notes)]
            events += _performance_pair(start + pos * beat, note, 70 + (index % 2) * 8, .16 * beat, 3, bar + index)
        if bar % 4 == 3:
            for index, note in enumerate(notes[:3]):
                events += _performance_pair(start + (3.45 + .13 * index) * beat, note + 12, 76 - index * 4, .11 * beat, 3, index)
    return events


def performance_pattern(intent: MusicIntent, bars: int = 8) -> list[MidiEvent]:
    """Eight-bar deterministic live-band arrangement for evaluation listening."""
    events: list[MidiEvent] = []
    # Channel setup: volume, pan, expression and send level create a static but intentional mix.
    events.extend((_cc(0, 1, 7, 96), _cc(0, 1, 10, 58), _cc(0, 1, 11, 105), _cc(0, 1, 91, 42)))
    events.extend((_cc(0, 2, 7, 105), _cc(0, 2, 10, 66), _cc(0, 2, 11, 112)))
    events.extend((_cc(0, 3, 7, 78), _cc(0, 3, 10, 74), _cc(0, 3, 91, 30)))
    if intent.parts.drums > 0:
        events.extend(_performance_drums(intent, bars))
    if intent.parts.bass > 0:
        events.extend(_performance_bass(intent, bars))
    if intent.parts.harmony > 0:
        events.extend(_performance_harmony(intent, bars))
        events.extend(_performance_ornament(intent, bars))
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))


def render_pattern(intent: MusicIntent, bars: int = 2, arrangement: str = "basic") -> list[MidiEvent]:
    if arrangement == "performance":
        return performance_pattern(intent, bars)
    if arrangement != "basic":
        raise ValueError(f"unsupported arrangement: {arrangement}")
    events: list[MidiEvent] = []
    if intent.parts.drums > 0: events.extend(drum_pattern(intent, bars))
    if intent.parts.bass > 0: events.extend(bass_pattern(intent, bars))
    if intent.parts.harmony > 0: events.extend(harmony_pattern(intent, bars))
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))
