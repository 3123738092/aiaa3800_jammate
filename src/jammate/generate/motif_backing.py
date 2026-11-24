"""Shape accompaniment around a hummed phrase without replacing its groove."""

from dataclasses import replace
from math import ceil

from jammate.generate.patterns import BASS_LOW
from jammate.music import BEATS_PER_BAR, Chord, Note


def has_melodic_contour(phrase: tuple[Note, ...]) -> bool:
    return len({note.start for note in phrase}) >= 3 and len({note.pitch for note in phrase}) >= 2


def _overlap(left: Note, right: Note) -> float:
    return min(left.end, right.end) - max(left.start, right.start)


def _separate_pitch(note: Note, melody: tuple[Note, ...], original: tuple[Note, ...],
                    voiced: list[Note]) -> Note:
    unsafe = {other.pitch + semitone for other in melody if _overlap(note, other) >= .05
              for semitone in (-1, 1)}
    if note.pitch not in unsafe:
        return note
    for pitch in (note.pitch - 12, note.pitch + 12):
        if not 45 <= pitch <= 96 or pitch in unsafe:
            continue
        if any(other.instrument == note.instrument and other.pitch == pitch and _overlap(note, other) > 0
               for other in (*original, *voiced)):
            continue
        return replace(note, pitch=pitch)
    return replace(note, velocity=max(1, round(note.velocity * .55)))


def follow_motif(backing: tuple[Note, ...], chord: Chord, phrase: tuple[Note, ...], bar: int, *,
                 pads=frozenset()) -> tuple[Note, ...]:
    """Use phrase contour, accent and space while keeping each existing attack."""
    first_pitch = min(phrase, key=lambda note: note.start).pitch
    length = ceil(max(note.end for note in phrase) / BEATS_PER_BAR)
    cycle, position = divmod(bar, length)
    start = position * BEATS_PER_BAR
    guide = tuple(note.shifted(-start) for note in phrase
                  if note.start < start + BEATS_PER_BAR and note.end > start)
    root = chord.voicing(BASS_LOW)[0]
    bass_tones = tuple(pitch for pitch in range(BASS_LOW, BASS_LOW + 20) if pitch % 12 in chord.pcs)
    references = {
        onset: max((note for note in guide if note.start <= onset + .125 and note.end > onset),
                   key=lambda note: note.start, default=None)
        for onset in {note.start for note in backing if note.instrument != "drums"}
    }
    voiced = []
    for note in backing:
        if note.instrument == "drums":
            voiced.append(note)
            continue
        reference = references[note.start]
        if note.instrument in pads:
            gain = .62 if reference is not None else .72
            duration = note.duration
        elif reference is None:
            gain = .72 if cycle % 2 == 0 else .53
            duration = min(note.duration, .25 if note.instrument == "bass" else .18)
        else:
            gain = .98 if reference.duration >= .75 else .84
            if abs(reference.start - note.start) <= .125:
                gain += .08
            duration = min(note.duration, max(.125, reference.end - note.start))
        note = replace(note, duration=duration, velocity=min(127, max(1, round(note.velocity * gain))))
        if note.instrument == "bass" and reference is not None and note.start > .125:
            interval = reference.pitch - first_pitch
            target = root + interval * (1 if cycle % 2 == 0 else .5)
            note = replace(note, pitch=min(bass_tones, key=lambda pitch: (abs(target - pitch), pitch)))
        elif note.instrument != "bass":
            note = _separate_pitch(note, guide, backing, voiced)
        voiced.append(note)
    return tuple(voiced)
