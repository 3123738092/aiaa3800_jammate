"""Phrase-aware performance for rule-generated backing."""
from dataclasses import replace

from jammate.generate.patterns import BASS_LOW, CLOSED_HAT, COMP_LOW, KICK, SNARE
from jammate.music import BEATS_PER_BAR, Note


_COMP_HITS = (
    ((0., .8), (2.5, .43)),
    ((.5, .55), (1.75, .32), (3., .52)),
    ((0., .6), (2., .4), (3.25, .36)),
    ((.5, .55), (1.75, .38)),
)
# Beat-relative offsets keep a protected groove intact across tempo changes.
# At the slowest supported tempo the largest offset is 14 ms.
_TIMING_BEATS = (-.007, -.004, 0., .003, .006)


def _inversions(chord):
    tones = chord.voicing(COMP_LOW)
    for split in range(len(tones)):
        form = (*tones[split:], *(pitch + 12 for pitch in tones[:split]))
        for octave in (-12, 0, 12):
            shifted = tuple(pitch + octave for pitch in form)
            if 52 <= shifted[0] <= shifted[-1] <= 76:
                yield shifted


def _voicing(chord, previous):
    anchor = min(_inversions(previous), key=lambda notes: abs(sum(notes) / len(notes) - 64))
    return min(_inversions(chord), key=lambda notes: (
        sum(min(abs(pitch - prior) for prior in anchor) for pitch in notes),
        abs(sum(notes) / len(notes) - 64),
    ))


def perform_backing(notes, chord, previous, following, bar, energy):
    """Shape generated parts before applying levels, density and playback edits."""
    phrase = bar % 4
    velocity = (0, -4, 3, -2, 2, -2, 5, -3)[bar % 8]
    instruments = {note.instrument for note in notes}
    result = []
    for note in notes:
        if note.instrument == "keys":
            continue
        if note.instrument == "bass":
            accent = 7 if note.start in {0., 2.} else -9
            note = replace(note, duration=note.duration * (.83 if energy == 1 else .75),
                           velocity=note.velocity + accent + velocity)
        elif note.instrument == "drums" and note.pitch == CLOSED_HAT:
            accent = -4 if note.start % 1 == 0 else -11 if note.start % .5 == 0 else -17
            note = replace(note, velocity=note.velocity + accent + velocity)
        result.append(note)

    if "keys" in instruments:
        hits = _COMP_HITS[phrase]
        if energy == 1:
            first, last = hits[0][0], hits[-1][0]
            hits = ((first, min(1.5, last - first - .025)), (last, .65))
        pitches = _voicing(chord, previous)
        result.extend(Note(pitch, beat, length, 66 + velocity - 6 * index + (pitch == pitches[-1]) * 3, "keys")
                      for index, (beat, length) in enumerate(hits) for pitch in pitches)

    if energy >= 2 and phrase in {1, 3} and "drums" in instruments:
        result.append(Note(SNARE, 2.75, .12, 36, "drums"))
    if energy >= 2 and phrase == 3 and following != chord and "bass" in instruments:
        root = following.voicing(BASS_LOW)[0]
        result = [replace(note, duration=min(note.duration, 3.72 - note.start))
                  if note.instrument == "bass" and note.start < 3.72 < note.end else note for note in result
                  if note.instrument != "bass" or note.start < 3.72]
        result.append(Note(root - 1, 3.76, .16, 59, "bass"))

    performed = []
    for note in result:
        if note.instrument == "keys" or (note.instrument == "drums" and note.pitch != KICK):
            index = bar + round(note.start * 16) + (note.pitch if note.instrument == "drums" else 0)
            start = min(BEATS_PER_BAR - .04, max(0., note.start + _TIMING_BEATS[index % len(_TIMING_BEATS)]))
            # Adjacent drum hits can move towards one another. Keep a note-off
            # gap so a repeated MIDI pitch remains unambiguous.
            duration = min(note.duration, .23) if note.instrument == "drums" else note.duration
            note = replace(note, start=start, duration=min(duration, BEATS_PER_BAR - start))
        performed.append(note)
    return tuple(performed)
