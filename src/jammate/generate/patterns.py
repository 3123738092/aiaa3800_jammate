"""One-bar backing patterns (drums, bass, keys) for each energy level.

Energy 0 is a "break": only a soft pad. Levels 1-3 add density. All notes
are bar-relative (start in [0, 4) beats); the arranger moves them in time.
"""

from __future__ import annotations

from jammate.music import Chord, Note

KICK, SNARE, CLOSED_HAT, OPEN_HAT, CRASH = 36, 38, 42, 46, 49
BASS_LOW = 36  # C2: lowest root the bass will play
COMP_LOW = 55  # G3: lowest root of the keyboard voicing

# energy -> (kick beats, snare beats, hat step in beats)
_DRUM_GRID = {
    1: ((0.0, 2.0), (), 1.0),
    2: ((0.0, 2.5), (1.0, 3.0), 0.5),
    3: ((0.0, 0.75, 2.0, 2.5), (1.0, 3.0), 0.25),
}


def _hit(pitch: int, start: float, velocity: int) -> Note:
    return Note(pitch=pitch, start=start, duration=0.25, velocity=velocity, instrument="drums")


def drum_bar(energy: int, phrase_pos: int) -> tuple[Note, ...]:
    """Drums for one bar; `phrase_pos` is the bar's position in a 4-bar phrase."""
    if energy <= 0:
        return ()
    kicks, snares, hat_step = _DRUM_GRID[min(energy, 3)]
    is_fill = energy >= 2 and phrase_pos == 3
    fill_from = 3.0 if energy >= 3 else 3.5
    hits = [_hit(KICK, b, 110) for b in kicks]
    hits += [_hit(SNARE, b, 100) for b in snares if not (is_fill and b >= fill_from)]
    steps = int(round(4.0 / hat_step))
    for i in range(steps):
        beat = i * hat_step
        if is_fill and beat >= fill_from:
            continue
        accent = 75 if beat == int(beat) else 55
        hits.append(_hit(CLOSED_HAT, beat, accent))
    if is_fill:
        n_fill = int(round((4.0 - fill_from) / 0.25))
        hits += [_hit(SNARE, fill_from + 0.25 * i, 85 + 5 * i) for i in range(n_fill)]
    if energy >= 3 and phrase_pos == 0:
        hits.append(_hit(CRASH, 0.0, 100))
    if energy >= 3 and not is_fill:
        hits.append(_hit(OPEN_HAT, 3.5, 70))
    return tuple(sorted(hits, key=lambda n: (n.start, n.pitch)))


def bass_bar(chord: Chord, energy: int) -> tuple[Note, ...]:
    if energy <= 0:
        return ()
    root = chord.voicing(BASS_LOW)[0]
    fifth = chord.voicing(BASS_LOW)[2]
    if energy == 1:
        rhythm = ((root, 0.0, 2.0), (root, 2.0, 2.0))
    elif energy == 2:
        rhythm = ((root, 0.0, 1.0), (fifth, 1.5, 0.5), (root, 2.0, 1.0), (root + 12, 3.0, 0.5),
                  (fifth, 3.5, 0.5))
    else:
        cycle = (root, root, fifth, root + 12)
        rhythm = tuple((cycle[i % 4], 0.5 * i, 0.5) for i in range(8))
    return tuple(Note(p, s, d, velocity=95, instrument="bass") for p, s, d in rhythm)


def comp_bar(chord: Chord, energy: int) -> tuple[Note, ...]:
    voicing = chord.voicing(COMP_LOW)
    if energy <= 1:
        hits = ((0.0, 4.0, 55 if energy == 0 else 65),)
    elif energy == 2:
        hits = ((0.0, 2.0, 70), (2.0, 2.0, 65))
    else:
        hits = tuple((b, 0.5, 72) for b in (0.5, 1.5, 2.5, 3.5))
    return tuple(
        Note(pitch, start, dur, velocity=vel, instrument="keys")
        for start, dur, vel in hits
        for pitch in voicing
    )
