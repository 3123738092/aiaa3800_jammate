"""Call-and-response: answer the user's motif, or throw a new one at them.

This is the core of the "I throw something, the AI catches it and throws
something back" loop. Answers are classic motivic transformations (echo,
sequence, inversion, retrograde, rhythmic displacement), snapped to the
current key, kept in a singable register and resolved onto the tonic chord
so the user hears a clear end of the AI's turn.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from jammate.generate.harmony import triad
from jammate.music import Key, Note

LEAD_LOW, LEAD_HIGH = 60, 84
TRANSFORMS = ("echo", "sequence", "inversion", "retrograde", "displace")
_SEQUENCE_STEPS = (2, -2, 3)
_DISPLACE_BEATS = 0.5
_LEAD_VELOCITY = 92
# (start, duration) templates for AI-initiated motifs; each fits one 4/4 bar.
_RHYTHMS = (
    ((0.0, 1.0), (1.0, 0.5), (1.5, 0.5), (2.0, 2.0)),
    ((0.0, 0.5), (0.5, 0.5), (1.0, 1.0), (2.0, 0.5), (2.5, 0.5), (3.0, 1.0)),
    ((0.0, 1.5), (1.5, 0.5), (2.0, 2.0)),
    ((0.0, 0.5), (0.5, 0.5), (1.0, 1.5), (3.0, 1.0)),
)
_WALK_STEPS = (-2, -1, 1, 1, 2)


@dataclass(frozen=True)
class Answer:
    notes: tuple[Note, ...]
    transform: str


def normalize(motif: tuple[Note, ...]) -> tuple[Note, ...]:
    """Sort by onset and move the first note to beat 0."""
    if not motif:
        return ()
    ordered = sorted(motif, key=lambda n: (n.start, n.pitch))
    origin = ordered[0].start
    return tuple(n.shifted(-origin) for n in ordered)


def _fold(pitch: int, low: int, high: int) -> int:
    while pitch < low:
        pitch += 12
    while pitch > high:
        pitch -= 12
    return pitch


def fit_register(pitches: list[int], low: int = LEAD_LOW, high: int = LEAD_HIGH) -> list[int]:
    """Shift by whole octaves to keep notes in range, preferring no shift."""

    def outside(ps: list[int]) -> int:
        return sum(1 for p in ps if p < low or p > high)

    octave = min(range(-3, 4), key=lambda k: (outside([p + 12 * k for p in pitches]), abs(k)))
    return [_fold(p + 12 * octave, low, high) for p in pitches]


def _resolve(pitch: int, key: Key, low: int = LEAD_LOW, high: int = LEAD_HIGH) -> int:
    targets = set(triad(key, 0).pcs)
    candidates = [p for p in range(low, high + 1) if p % 12 in targets]
    return min(candidates, key=lambda p: (abs(p - pitch), p))


def _finish(rhythm: list[tuple[float, float]], pitches: list[int], key: Key) -> tuple[Note, ...]:
    in_key = fit_register([key.snap(p) for p in pitches])
    in_key[-1] = _resolve(in_key[-1], key)
    return tuple(
        Note(pitch, start, duration, velocity=_LEAD_VELOCITY, instrument="lead")
        for pitch, (start, duration) in zip(in_key, rhythm)
    )


def _displace(rhythm: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Hold the first note longer and push the rest off the beat."""
    span = max(start + dur for start, dur in rhythm)
    shifted = [(rhythm[0][0], rhythm[0][1] + _DISPLACE_BEATS)]
    shifted += [(start + _DISPLACE_BEATS, dur) for start, dur in rhythm[1:]]
    kept = [(start, min(dur, span - start)) for start, dur in shifted if start < span]
    return kept


def _transform_pitches(name: str, pitches: list[int], key: Key, rng: random.Random) -> list[int]:
    if name in ("echo", "displace"):
        return list(pitches)
    if name == "sequence":
        step = rng.choice(_SEQUENCE_STEPS)
        return [key.transpose_diatonic(p, step) for p in pitches]
    if name == "inversion":
        pivot = key.to_index(pitches[0])
        return [key.from_index(2 * pivot - key.to_index(p)) for p in pitches]
    if name == "retrograde":
        return list(reversed(pitches))
    raise ValueError(f"unknown transform {name!r}; expected one of {TRANSFORMS}")


def _build(name: str, notes: tuple[Note, ...], key: Key, rng: random.Random) -> Answer:
    pitches = _transform_pitches(name, [n.pitch for n in notes], key, rng)
    rhythm = [(n.start, n.duration) for n in notes]
    if name == "displace":
        rhythm = _displace(rhythm)
        pitches = pitches[: len(rhythm)]
    return Answer(_finish(rhythm, pitches, key), name)


def _pitch_classes(notes: tuple[Note, ...]) -> list[int]:
    return [n.pitch % 12 for n in notes]


def answer_motif(
    motif: tuple[Note, ...], key: Key, rng: random.Random, transform: str | None = None
) -> Answer:
    """Answer with `transform`, or with a random one that is not a plain copy.

    A random answer must change the melody (pitch classes), otherwise e.g. the
    retrograde of a palindrome sounds like the AI just repeating the user.
    """
    notes = normalize(motif)
    if not notes:
        return Answer((), "none")
    if transform is not None:
        return _build(transform, notes, key, rng)
    heard = _pitch_classes(notes)
    candidates = [name for name in TRANSFORMS if name != "echo"]
    rng.shuffle(candidates)
    for name in candidates:
        answer = _build(name, notes, key, rng)
        if _pitch_classes(answer.notes) != heard:
            return answer
    return _build("echo", notes, key, rng)


def initiate_motif(key: Key, rng: random.Random) -> tuple[Note, ...]:
    """A short, stepwise motif starting on a tonic-chord tone: easy to sing back."""
    rhythm = list(rng.choice(_RHYTHMS))
    tonic_pitch = LEAD_LOW + (key.tonic - LEAD_LOW) % 12
    index = key.to_index(tonic_pitch) + rng.choice((0, 2, 4))
    pitches = []
    for _ in rhythm:
        pitches.append(key.from_index(index))
        step = rng.choice(_WALK_STEPS)
        if not LEAD_LOW <= key.from_index(index + step) <= LEAD_HIGH - 3:
            step = -step
        index += step
    return _finish(rhythm, pitches, key)
