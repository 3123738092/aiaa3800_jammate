"""Rhythm-conditioned drums, bass and keys, in bar-relative beats.

All parts use one rhythmic skeleton. Variation keeps the input anchors and
adds at most a few sixteenth-note ornaments. Simplification keeps the
strongest, separated anchors. Tempo is applied only by the renderer.
"""

from __future__ import annotations

from jammate.generate.patterns import BASS_LOW, CLOSED_HAT, COMP_LOW, KICK, SNARE
from jammate.music import BEATS_PER_BAR, Chord, Note
from jammate.rhythm import RhythmContext, RhythmMode


def rhythmic_hits(context: RhythmContext, mode: RhythmMode,
                  bar_index: int, energy: int) -> tuple[tuple[float, float], ...]:
    mode = RhythmMode(mode)
    hits = list(zip(context.onset_beats, context.accents))
    if not hits:
        return ()
    if mode is RhythmMode.SIMPLIFY:
        selected: list[tuple[float, float]] = []
        # Prefer strong accents; allow at most one anchor per beat.
        for hit in sorted(hits, key=lambda h: (-h[1], h[0])):
            if all(abs(hit[0] - kept[0]) >= 1.0 for kept in selected):
                selected.append(hit)
            if len(selected) == 2:
                break
        return tuple(sorted(selected))
    if mode is RhythmMode.VARY and energy > 0:
        occupied = {b for b, _ in hits}
        candidates = [i * 0.25 for i in range(1, 16)
                      if all(abs(i * 0.25 - b) >= 0.25 - 1e-9 for b in occupied)]
        # The last bar of each phrase gets a short fill; other bars rotate
        # their ornament positions deterministically, preserving reproducibility.
        candidates.sort(key=lambda b: (b < 3 if bar_index % 4 == 3 else False, b))
        if candidates:
            offset = 0 if bar_index % 4 == 3 else bar_index % len(candidates)
            candidates = candidates[offset:] + candidates[:offset]
            hits += [(b, 0.4) for b in candidates[:min(energy, 3)]]
    return tuple(sorted(hits))


def rhythm_parts(chord: Chord, context: RhythmContext, mode: RhythmMode,
                 energy: int, bar_index: int) -> tuple[Note, ...]:
    """Generate a coordinated bar. Energy zero remains the existing pad break."""
    if energy <= 0:
        return tuple(Note(p, 0, BEATS_PER_BAR, 45, "keys") for p in chord.voicing(COMP_LOW))
    hits = rhythmic_hits(context, mode, bar_index, energy)
    if not hits:
        return ()
    root, _, fifth = chord.voicing(BASS_LOW)
    voicing = chord.voicing(COMP_LOW)
    notes: list[Note] = []
    for i, (beat, accent) in enumerate(hits):
        next_beat = hits[i + 1][0] if i + 1 < len(hits) else float(BEATS_PER_BAR)
        duration = min(next_beat - beat, 0.75)
        velocity = min(127, round((55 + 15 * energy) * (0.45 + 0.55 * accent)))
        drum = KICK if i % 2 == 0 else SNARE
        notes.append(Note(drum, beat, min(0.125, duration), velocity, "drums"))
        notes.append(Note(CLOSED_HAT, beat, min(0.125, duration), max(1, velocity - 25), "drums"))
        notes.append(Note(root if i % 2 == 0 else fifth, beat, duration, velocity, "bass"))
        notes.extend(Note(p, beat, min(duration, 0.5), max(1, velocity - 25), "keys") for p in voicing)
    return tuple(sorted(notes, key=lambda n: (n.start, n.instrument, n.pitch)))
