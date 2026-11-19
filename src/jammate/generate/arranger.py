"""Turn the jam state into concrete notes, one bar at a time."""

from __future__ import annotations

from collections import defaultdict

from jammate.generate.harmony import harmony_cycle
from jammate.generate.patterns import added_instrument_bar, bass_bar, comp_bar, drum_bar
from jammate.generate.rhythm import rhythm_parts
from jammate.generate.texture import apply_texture
from jammate.instruments import added_program
from jammate.music import BEATS_PER_BAR, Chord, Note
from jammate.state import JamState
from jammate.texture import ArrangementTexture

PHRASE_BARS = 4


def chord_for_bar(state: JamState, bar_index: int) -> Chord:
    loop = harmony_cycle(state)
    return loop[(bar_index - state.progression_start_bar) % len(loop)]


def arrange_bar(state: JamState, bar_index: int, lead: tuple[Note, ...] = ()) -> tuple[Note, ...]:
    """All parts for one bar in absolute beats. `lead` is bar-relative."""
    if not state.playing:
        return ()
    chord = chord_for_bar(state, bar_index)
    phrase_pos = bar_index % PHRASE_BARS
    if state.rhythm is not None:
        backing = rhythm_parts(chord, state.rhythm, state.rhythm_mode, state.energy, bar_index)
    else:
        backing = (drum_bar(state.energy, phrase_pos)
                   + bass_bar(chord, state.energy)
                   + comp_bar(chord, state.energy))
    for layer in state.layers:
        if layer.active and added_program(layer.name) is not None:
            backing += added_instrument_bar(layer.name, chord, phrase_pos)
    if state.texture != ArrangementTexture():
        backing = apply_texture(state, chord, backing)
    bar_notes = backing + tuple(lead)
    offset = bar_index * BEATS_PER_BAR
    return tuple(n.shifted(offset) for n in bar_notes)


def place_lead(motif: tuple[Note, ...], start_bar: int) -> dict[int, tuple[Note, ...]]:
    """Split a motif (beat 0 = its first downbeat) into bar-relative chunks."""
    bars: dict[int, list[Note]] = defaultdict(list)
    for note in motif:
        bar_offset = int(note.start // BEATS_PER_BAR)
        bars[start_bar + bar_offset].append(note.shifted(-bar_offset * BEATS_PER_BAR))
    return {bar: tuple(notes) for bar, notes in sorted(bars.items())}
