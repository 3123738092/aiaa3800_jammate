"""Export the band's notes as a multi-track General MIDI file."""

from __future__ import annotations

from pathlib import Path

import pretty_midi

from jammate.music import Note

# General MIDI programs (0-based): finger bass, electric piano, square lead, choir.
_PROGRAMS = {"bass": 33, "keys": 4, "lead": 80, "user": 52}


def to_pretty_midi(notes: tuple[Note, ...], tempo_bpm: float) -> pretty_midi.PrettyMIDI:
    seconds_per_beat = 60.0 / tempo_bpm
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo_bpm)
    tracks: dict[str, pretty_midi.Instrument] = {}
    for note in sorted(notes, key=lambda n: (n.start, n.pitch)):
        track = tracks.get(note.instrument)
        if track is None:
            is_drum = note.instrument == "drums"
            track = pretty_midi.Instrument(
                program=0 if is_drum else _PROGRAMS.get(note.instrument, 0),
                is_drum=is_drum, name=note.instrument,
            )
            tracks[note.instrument] = track
        track.notes.append(pretty_midi.Note(
            velocity=note.velocity, pitch=note.pitch,
            start=note.start * seconds_per_beat, end=note.end * seconds_per_beat,
        ))
    pm.instruments.extend(tracks.values())
    return pm


def write_midi(notes: tuple[Note, ...], tempo_bpm: float, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_pretty_midi(notes, tempo_bpm).write(str(path))
    return path
