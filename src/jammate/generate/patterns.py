"""One-bar backing patterns (drums, bass, keys) for each energy level.

Energy 0 is a "break": only a soft pad. Levels 1-3 add density. All notes
are bar-relative (start in [0, 4) beats); the arranger moves them in time.
"""

from __future__ import annotations

from jammate.instruments import gm_program
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


def guitar_bar(chord: Chord) -> tuple[Note, ...]:
    """A sparse eighth-note acoustic-guitar arpeggio in the current chord."""
    root, third, fifth = chord.voicing(55)[:3]
    return tuple(Note(pitch, beat, .45, velocity, "guitar")
                 for pitch, beat, velocity in ((root, .5, 64), (fifth, 1.5, 55),
                                               (third, 2.5, 60), (fifth, 3.5, 54)))


def added_instrument_bar(instrument: str, chord: Chord, phrase_pos: int) -> tuple[Note, ...]:
    """Harmonize an added instrument in a register and rhythm suited to its role."""
    if instrument == "guitar":
        return guitar_bar(chord)
    program = gm_program(instrument)
    family = program // 8 if program is not None else None
    if instrument == "flute" or family in {7, 8, 9, 13, 15}:
        root, third, fifth = chord.voicing(72)[:3]
        if phrase_pos % 2:
            part = ((fifth, 1.75, .45, 63), (third, 2.25, .45, 59), (root, 3., .8, 61))
        else:
            part = ((root, 1.5, .7, 61), (fifth, 2.75, .8, 64))
    elif instrument == "strings" or family in {2, 5, 6, 11, 12}:
        root, _, fifth = chord.voicing(55)[:3]
        part = ((root, 0., 3.8, 53), (fifth, 0., 3.8, 45))
    elif instrument == "harp" or family in {0, 1, 3, 10, 14}:
        root, third, fifth = chord.voicing(60)[:3]
        part = ((root, .25, .55, 65), (third, .75, .5, 54), (fifth, 1.25, .6, 60),
                (root + 12, 2.25, .55, 57), (fifth, 3., .7, 49))
    elif family == 4:
        root, _, fifth = chord.voicing(BASS_LOW)[:3]
        part = ((root, .5, .85, 62), (fifth, 2., .7, 56), (root + 12, 3.25, .5, 55))
    else:
        raise ValueError(f"no arrangement for instrument {instrument!r}")
    return tuple(Note(pitch, beat, duration, velocity, instrument) for pitch, beat, duration, velocity in part)


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
