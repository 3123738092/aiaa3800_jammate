from __future__ import annotations

import json
import math
from pathlib import Path
from .events import MidiEvent


def write_midi(path: Path, events: list[MidiEvent], bpm: float, programs: dict[int, int] | None = None,
               *, duration_s: float | None = None) -> None:
    """Write absolute ticks, retaining trailing rests and explicit note cleanup.

    Rounding each delta independently accumulates drift for unquantized humming.
    Convert seconds using the tempo actually encoded in the MIDI file instead.
    """
    import mido
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("bpm must be finite and positive")
    if duration_s is not None and (not math.isfinite(duration_s) or duration_s < 0):
        raise ValueError("duration_s must be finite and nonnegative")
    tempo = mido.bpm2tempo(bpm)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="JamMate"))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    for channel, program in sorted((programs or {}).items()):
        track.append(mido.Message("program_change", channel=channel, program=program, time=0))
    def order(event):
        if not math.isfinite(event.timestamp) or event.timestamp < 0:
            raise ValueError("event timestamp must be finite and nonnegative")
        # Adjacent decimal-rounded source notes can differ by a microsecond.
        # At one MIDI tick, release the old voice before starting the next one.
        return round(mido.second2tick(event.timestamp, 480, tempo)), event.type == "note_on"

    previous = 0
    for event in sorted(events, key=order):
        if duration_s is not None and event.timestamp > duration_s + 1e-9:
            raise ValueError("event extends beyond the declared duration")
        tick = order(event)[0]
        ticks = tick - previous
        if event.type in ("note_on", "note_off"):
            track.append(mido.Message(event.type, channel=event.channel, note=event.note, velocity=event.velocity, time=ticks))
        elif event.type == "control_change":
            track.append(mido.Message("control_change", channel=event.channel, control=event.control, value=event.value, time=ticks))
        elif event.type == "all_notes_off":
            track.append(mido.Message("control_change", channel=event.channel, control=123, value=0, time=ticks))
        else:
            raise ValueError(f"unsupported MIDI event: {event.type}")
        previous = tick
    end = previous if duration_s is None else round(mido.second2tick(duration_s, 480, tempo))
    track.append(mido.MetaMessage("end_of_track", time=end - previous))
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
