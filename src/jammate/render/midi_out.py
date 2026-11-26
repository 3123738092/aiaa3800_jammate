"""Export the band's notes as a multi-track General MIDI file."""

from __future__ import annotations

from pathlib import Path

import pretty_midi

from jammate.instruments import ADDED_INSTRUMENTS, gm_program
from jammate.music import Note

# General MIDI programs are 0-based.
GUITAR_PROGRAM = ADDED_INSTRUMENTS["guitar"].midi_program
_PROGRAMS = {"bass": 33, "keys": 4, "lead": 80, "user": 52,
             **{name: instrument.midi_program for name, instrument in ADDED_INSTRUMENTS.items()}}
_PERFORMANCE_MIX = {
    # Channel volume, expression, reverb send.
    "drums": (98, 122, 9), "bass": (106, 123, 4), "keys": (100, 118, 26),
    "lead": (105, 126, 18),
}


def midi_program(instrument, programs=None):
    program = gm_program(instrument)
    return (programs or {}).get(instrument, _PROGRAMS.get(instrument, 0)) if program is None else program


def to_pretty_midi(notes: tuple[Note, ...], tempo_bpm: float, *, programs=None, performance_mix=False) -> pretty_midi.PrettyMIDI:
    seconds_per_beat = 60.0 / tempo_bpm
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo_bpm)
    tracks: dict[str, pretty_midi.Instrument] = {}
    for note in sorted(notes, key=lambda n: (n.start, n.pitch)):
        track = tracks.get(note.instrument)
        if track is None:
            is_drum = note.instrument == "drums"
            track = pretty_midi.Instrument(
                program=midi_program(note.instrument, programs),
                is_drum=is_drum, name=note.instrument,
            )
            if performance_mix:
                levels = _PERFORMANCE_MIX.get(note.instrument, (100, 118, 18))
                track.control_changes.extend(pretty_midi.ControlChange(cc, value, 0.)
                                             for cc, value in zip((7, 11, 91), levels))
            tracks[note.instrument] = track
        track.notes.append(pretty_midi.Note(
            velocity=note.velocity, pitch=note.pitch,
            start=note.start * seconds_per_beat, end=note.end * seconds_per_beat,
        ))
    pm.instruments.extend(tracks.values())
    return pm


def write_midi(notes: tuple[Note, ...], tempo_bpm: float, path: Path, *, programs=None, performance_mix=False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_pretty_midi(notes, tempo_bpm, programs=programs, performance_mix=performance_mix).write(str(path))
    return path
