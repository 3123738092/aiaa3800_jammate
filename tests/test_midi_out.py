import pretty_midi
import pytest

from jammate.music import Note
from jammate.render.midi_out import to_pretty_midi, write_midi

pytestmark = pytest.mark.unit

NOTES = (
    Note(36, 0.0, 0.25, instrument="drums"),
    Note(40, 0.0, 2.0, instrument="bass"),
    Note(72, 2.0, 1.0, instrument="lead"),
)


def test_tracks_are_split_by_instrument_with_drums_flagged():
    pm = to_pretty_midi(NOTES, tempo_bpm=120.0)
    by_name = {inst.name: inst for inst in pm.instruments}
    assert set(by_name) == {"drums", "bass", "lead"}
    assert by_name["drums"].is_drum and not by_name["bass"].is_drum


def test_note_times_are_converted_to_seconds():
    pm = to_pretty_midi(NOTES, tempo_bpm=120.0)
    lead = next(i for i in pm.instruments if i.name == "lead").notes[0]
    assert lead.start == pytest.approx(1.0) and lead.end == pytest.approx(1.5)


def test_write_midi_round_trips(tmp_path):
    path = write_midi(NOTES, tempo_bpm=100.0, path=tmp_path / "jam.mid")
    loaded = pretty_midi.PrettyMIDI(str(path))
    assert sum(len(i.notes) for i in loaded.instruments) == len(NOTES)
