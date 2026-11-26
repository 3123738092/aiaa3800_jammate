"""Turn the jam state into concrete notes, one bar at a time."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from jammate.generate.harmony import harmony_cycle
from jammate.generate.motif_backing import follow_motif
from jammate.generate.patterns import added_instrument_bar, bass_bar, comp_bar, drum_bar
from jammate.generate.performance import perform_backing
from jammate.generate.rhythm import rhythm_parts, rhythmic_hits
from jammate.generate.texture import apply_texture
from jammate.instruments import added_program
from jammate.music import BEATS_PER_BAR, Chord, Note
from jammate.state import JamState
from jammate.texture import ArrangementTexture

PHRASE_BARS = 4


def chord_for_bar(state: JamState, bar_index: int) -> Chord | None:
    loop = harmony_cycle(state)
    return loop[(bar_index - state.progression_start_bar) % len(loop)] if loop else None


def follow_added_rhythm(part, hits, beats_per_bar=BEATS_PER_BAR):
    groups = defaultdict(list)
    for note in part:
        groups[note.start].append(note)
    phrases = tuple(groups.values())
    return tuple(
        replace(note, start=beat, duration=min(note.duration, end - beat),
                velocity=max(1, round(note.velocity * (.55 + .45 * accent))))
        for i, ((beat, accent), end) in enumerate(zip(hits, [x[0] for x in hits[1:]] + [beats_per_bar]))
        for note in phrases[i % len(phrases)]
    ) if phrases else ()


def arrange_bar(state: JamState, bar_index: int, lead: tuple[Note, ...] = ()) -> tuple[Note, ...]:
    """All parts for one bar in absolute beats. `lead` is bar-relative."""
    if not state.playing:
        return ()
    chord = chord_for_bar(state, bar_index)
    phrase_pos = bar_index % PHRASE_BARS
    if state.rhythm is not None:
        backing = rhythm_parts(chord, state.rhythm, state.rhythm_mode, state.energy, bar_index)
    else:
        backing = drum_bar(state.energy, phrase_pos)
        if chord is not None:
            backing += tuple(note for note in bass_bar(chord, state.energy) + comp_bar(chord, state.energy)
                             if note.start < state.beats_per_bar)
            if state.energy > 0 and not (state.accompaniment_motif and bar_index >= state.accompaniment_motif.start_bar):
                backing = perform_backing(backing, chord, chord_for_bar(state, max(0, bar_index - 1)),
                                          chord_for_bar(state, bar_index + 1), bar_index, state.energy)
    for layer in state.layers:
        if chord is not None and layer.active and added_program(layer.name) is not None:
            part = tuple(note for note in added_instrument_bar(layer.name, chord, phrase_pos)
                         if note.start < state.beats_per_bar)
            if state.rhythm is not None:
                part = follow_added_rhythm(part, rhythmic_hits(state.rhythm, state.rhythm_mode, bar_index, state.energy),
                                           state.beats_per_bar)
            backing += part
    if state.texture != ArrangementTexture():
        backing = tuple(note for note in apply_texture(state, chord, backing)
                        if note.start < state.beats_per_bar)
    theme = state.accompaniment_motif
    if chord is not None and theme is not None and bar_index >= theme.start_bar:
        pads = {layer.name for layer in state.layers if layer.active and layer.role == "pad"}
        backing = follow_motif(backing, chord, state.motif(theme.motif_id).notes,
                              bar_index - theme.start_bar, pads=pads)
    if state.beats_per_bar < 4:
        backing = tuple(replace(note, duration=min(note.duration, state.beats_per_bar - note.start))
                        for note in backing if note.start < state.beats_per_bar)
    bar_notes = backing + tuple(lead)
    offset = bar_index * state.beats_per_bar
    return tuple(n.shifted(offset) for n in bar_notes)


def place_lead(motif: tuple[Note, ...], start_bar: int, beats_per_bar: int = BEATS_PER_BAR) -> dict[int, tuple[Note, ...]]:
    """Split a motif (beat 0 = its first downbeat) into bar-relative chunks."""
    bars: dict[int, list[Note]] = defaultdict(list)
    for note in motif:
        bar_offset = int(note.start // beats_per_bar)
        bars[start_bar + bar_offset].append(note.shifted(-bar_offset * beats_per_bar))
    return {bar: tuple(notes) for bar, notes in sorted(bars.items())}
