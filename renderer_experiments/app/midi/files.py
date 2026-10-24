from __future__ import annotations

import json
from pathlib import Path
from .events import MidiEvent


def write_midi(path: Path, events: list[MidiEvent], bpm: float, programs: dict[int, int] | None = None) -> None:
    """Write a type-0 Standard MIDI file without requiring a DAW."""
    import mido
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="JamMate"))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    for channel, program in sorted((programs or {}).items()):
        track.append(mido.Message("program_change", channel=channel, program=program, time=0))
    previous = 0.0
    for event in sorted(events, key=lambda e: (e.timestamp, e.type == "note_on")):
        delta = max(0, event.timestamp - previous); previous = event.timestamp
        ticks = round(delta * 480 * bpm / 60)
        if event.type in ("note_on", "note_off"):
            track.append(mido.Message(event.type, channel=event.channel, note=event.note, velocity=event.velocity, time=ticks))
        elif event.type == "control_change":
            track.append(mido.Message("control_change", channel=event.channel, control=event.control, value=event.value, time=ticks))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid = mido.MidiFile(type=0, ticks_per_beat=480); mid.tracks.append(track)
    path.parent.mkdir(parents=True, exist_ok=True); mid.save(path)


def write_events(path: Path, events: list[MidiEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([e.__dict__ for e in events], indent=2), encoding="utf-8")


def render_with_fluidsynth(midi_path: Path, wav_path: Path, executable: Path, soundfont: Path, sample_rate: int = 44100) -> None:
    import subprocess
    if not executable.is_file(): raise FileNotFoundError(f"FluidSynth executable not found: {executable}")
    if not soundfont.is_file(): raise FileNotFoundError(f"SoundFont not found: {soundfont}")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(executable), "-ni", "-F", str(wav_path), "-r", str(sample_rate), "-g", "0.8", str(soundfont), str(midi_path)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(f"FluidSynth failed ({completed.returncode}): {(completed.stderr or completed.stdout)[-2000:]}")
    if not wav_path.is_file() or wav_path.stat().st_size <= 44:
        raise RuntimeError("FluidSynth completed without producing audio data")
