"""Standalone percussion MIDI on the free rhythm's recording timeline."""

import io

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo


ENCODING_TEMPO_BPM = 120
TICKS_PER_BEAT = 24000
MIDI_TICK_COLLISION = "节拍位置太接近，无法写入独立 MIDI 拍点。"


def ticks_per_second(tempo_bpm):
    return TICKS_PER_BEAT * 1_000_000 / bpm2tempo(tempo_bpm)


def free_rhythm_ticks(onset_seconds, duration_s, tempo_bpm=ENCODING_TEMPO_BPM):
    rate = ticks_per_second(tempo_bpm)
    end_tick = round(duration_s * rate)
    starts = [min(round(time * rate), end_tick - 1) for time in onset_seconds]
    if any(a >= b for a, b in zip(starts, starts[1:])):
        raise ValueError(MIDI_TICK_COLLISION)
    return starts, end_tick, rate


def free_rhythm_midi(rhythm, duration_s):
    starts, end_tick, rate = free_rhythm_ticks(rhythm["onset_seconds"], duration_s)

    midi = MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
    track = MidiTrack()
    midi.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=bpm2tempo(ENCODING_TEMPO_BPM)))
    track.append(MetaMessage("track_name", name="drums"))
    hits = []
    cursor = 0
    for start, next_start, accent in zip(starts, (*starts[1:], end_tick), rhythm["accents"]):
        length = min(round(.0625 * rate), next_start - start)
        velocity = max(1, round(accent * 127))
        track.append(Message("note_on", channel=9, note=36, velocity=velocity, time=start - cursor))
        track.append(Message("note_off", channel=9, note=36, velocity=0, time=length))
        cursor = start + length
        hits.append({"onset_s": start / rate, "velocity": velocity, "duration_s": length / rate})
    track.append(MetaMessage("end_of_track", time=end_tick - cursor))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue(), hits
