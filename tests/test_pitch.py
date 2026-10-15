import numpy as np
import pytest

from jammate.analysis.pitch import TimedNote, segment_notes, to_beats, track_pitch

FRAME = 0.01  # 10 ms analysis frames for the synthetic tracks


def _track(segments):
    """segments: list of (midi or None, seconds) -> times, midi, voiced arrays."""
    values = []
    for midi, seconds in segments:
        values += [np.nan if midi is None else midi] * int(round(seconds / FRAME))
    midi = np.array(values, dtype=float)
    return np.arange(midi.size) * FRAME, midi, np.isfinite(midi)


@pytest.mark.unit
def test_segment_notes_splits_on_pitch_change_and_silence():
    times, midi, voiced = _track([(60, 0.30), (None, 0.10), (64, 0.20), (67, 0.40)])
    notes = segment_notes(times, midi, voiced)
    assert [n.pitch for n in notes] == [60, 64, 67]
    assert notes[0].start_s == pytest.approx(0.0)
    assert notes[0].end_s == pytest.approx(0.30, abs=0.02)
    assert notes[2].end_s == pytest.approx(1.00, abs=0.02)


@pytest.mark.unit
def test_vibrato_and_short_glitches_do_not_create_notes():
    times, midi, voiced = _track([(60.0, 0.5)])
    wobble = midi + 0.45 * np.sin(np.arange(midi.size) * 0.9)  # crosses the rounding edge
    wobble[20:23] = 67.0  # a 30 ms octave-ish glitch
    notes = segment_notes(times, wobble, voiced)
    assert [n.pitch for n in notes] == [60]


@pytest.mark.unit
def test_tiny_unvoiced_gap_inside_a_note_is_bridged():
    times, midi, voiced = _track([(62, 0.2), (None, 0.03), (62, 0.2)])
    assert [n.pitch for n in segment_notes(times, midi, voiced)] == [62]


@pytest.mark.unit
def test_to_beats_quantizes_to_sixteenths():
    notes = (TimedNote(60, 1.02, 1.49), TimedNote(64, 1.50, 2.27), TimedNote(67, 0.2, 0.4))
    beats = to_beats(notes, tempo_bpm=120.0, origin_s=1.0)
    # 0.5 s per beat; the note before the origin is dropped
    assert [(n.pitch, n.start, n.duration) for n in beats] == [(60, 0.0, 1.0), (64, 1.0, 1.5)]
    assert all(n.instrument == "user" for n in beats)


@pytest.mark.unit
def test_to_beats_trims_overlaps_after_quantization():
    notes = (TimedNote(60, 0.0, 0.62), TimedNote(62, 0.55, 1.0))
    beats = to_beats(notes, tempo_bpm=120.0, origin_s=0.0)
    assert beats[0].end <= beats[1].start


@pytest.mark.integration
def test_track_pitch_on_a_sung_like_tone():
    sr = 22050
    t = np.arange(int(1.0 * sr)) / sr
    f0 = 220.0 * 2 ** (0.1 * np.sin(2 * np.pi * 5 * t) / 12)  # A3 with gentle vibrato
    phase = 2 * np.pi * np.cumsum(f0) / sr
    y = (0.5 * np.sin(phase) + 0.2 * np.sin(2 * phase)).astype(np.float32)
    times, midi, voiced = track_pitch(y, sr)
    assert voiced.mean() > 0.8
    assert np.nanmedian(midi[voiced]) == pytest.approx(57.0, abs=0.3)
