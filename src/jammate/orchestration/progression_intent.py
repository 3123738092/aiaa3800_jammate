"""Explicit chord names and open-ended progression requests."""
from dataclasses import asdict
import re

from jammate.generate.harmony import harmony_cycle, same_cycle
from jammate.music import Chord, ChordStep, PITCH_CLASS_NAMES
from .explicit_request import request_hold
from .plans import make_plan
from .tempo_intent import speech_clauses


_OPEN = re.compile(
    r"(?:能不能|可不可以|可以|请|帮我)?\s*(?:再)?(?:换|改)(?:一个|个|一组|一套)?(?:新的|不同的)?"
    r"(?:和弦进行|和弦走向)(?:吧|吗)?"
    r"|(?:please\s+)?(?:change|switch|try)\s+(?:to\s+)?(?:a different|another|the)\s+chord progression"
)
_EXPLICIT = re.compile(
    r"(?:请\s*)?(?:把)?(?:和弦进行|和弦走向|和弦)\s*(?:改成|改为|换成|换为|设为|设置为)\s*(.+)"
    r"|(?:please\s+)?(?:change|switch|set)\s+(?:the\s+)?(?:chord\s+)?progression\s+to\s+(.+)"
)
_CHORD = re.compile(r"([a-g])([#♯b♭]?)(maj7|min7|m7|maj|min|dim|m|7)?")
_NATURALS = {name.lower(): pc for pc, name in enumerate(PITCH_CLASS_NAMES) if len(name) == 1}
_ACCIDENTALS = {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}
_QUALITIES = {None: "maj", "maj": "maj", "m": "min", "min": "min", "dim": "dim", "7": "dom7",
              "maj7": "maj7", "m7": "min7", "min7": "min7"}


def asks_for_new_progression(events):
    clauses = speech_clauses(events)
    return len(clauses) == 1 and _OPEN.fullmatch(clauses[0]) is not None


def explicit_progression_plan(events, state, plan_id):
    clauses = speech_clauses(events)
    if len(clauses) != 1 or (match := _EXPLICIT.fullmatch(clauses[0])) is None:
        return None
    text = match[1] or match[2]
    tokens = re.split(r"\s*[-–—→、]\s*|\s+", text.strip())
    matches = [_CHORD.fullmatch(token) for token in tokens]
    if not 2 <= len(matches) <= 8 or not all(matches):
        return None
    if state.key is None:
        return request_hold(events, state, plan_id, "请先设置当前调性，再切换和弦进行。")
    chords = tuple(
        Chord((_NATURALS[root] + _ACCIDENTALS[accidental]) % 12, _QUALITIES[quality])
        for root, accidental, quality in (chord.groups() for chord in matches)
    )
    label = " → ".join(chord.name for chord in chords)
    if len(set(chords)) < 2:
        return request_hold(events, state, plan_id, "请给出至少两个不同的和弦来组成进行。")
    if same_cycle(harmony_cycle(state), chords):
        return request_hold(events, state, plan_id, f"当前已使用这组和弦进行（{label}）。")
    steps = [ChordStep((chord.root - state.key.tonic) % 12, chord.quality) for chord in chords]
    preserve = ["tempo", "key", "playing", "groove", "layer:drums"]
    if state.current_motif_id:
        preserve.append(f"motif:{state.current_motif_id}")
    action = {"action": "SET_PROGRESSION", "scope": "band", "strength": 1,
              "parameters": {"chords": [asdict(step) for step in steps]}}
    return make_plan(state, events, plan_id, "FOLLOW", [action], preserve, {}, len(steps),
                     f"从生效小节起使用 {label}，每小节一个和弦。", [])
