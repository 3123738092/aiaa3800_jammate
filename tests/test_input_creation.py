"""Check what the listener actually gets back from one input."""
import base64
import io
import json
from pathlib import Path

import pretty_midi
import pytest

from jammate.input_band import render_clip


def source(kind):
    folder = Path(__file__).resolve().parents[1] / "examples" / "web_band"
    return json.loads((folder / f"{kind}.agent.json").read_text())


def read_midi(result):
    return pretty_midi.PrettyMIDI(io.BytesIO(base64.b64decode(result["midi"])))


def test_humming_uses_its_phrase_as_a_seed_for_a_new_lead():
    event = source("humming")
    response = render_clip(event, creative=True)
    lead = next(part.notes for part in read_midi(response).instruments if part.name == "lead")
    original = event["payload"]["melody"]["notes"]
    assert [note.pitch for note in lead] != [note["pitchMidi"] for note in original]
    source_gaps = [b["startSec"] - a["startSec"] for a, b in zip(original, original[1:])]
    answer_gaps = [b.start - a.start for a, b in zip(lead, lead[1:])]
    assert sum(abs(a - b) < .003 for a, b in zip(source_gaps, answer_gaps)) >= 2
    assert response["plan"]["trigger_event_ids"] == [event["event_id"]]


def test_rhythm_creates_extra_hits_without_losing_source_anchors_or_tempo():
    event = source("rhythm")
    rhythm = event["payload"]["rhythm"]
    result = render_clip(event, creative=True)
    midi = read_midi(result)
    assert midi.get_tempo_changes()[1][0] == pytest.approx(rhythm["tempo_bpm"], abs=.001)
    drums = next(part for part in midi.instruments if part.is_drum)
    beats = {round(note.start * rhythm["tempo_bpm"] / 60, 2) for note in drums.notes}
    first_bar = {beat for beat in beats if beat < 4}
    assert set(rhythm["onset_beats"]) < first_bar
    assert result["plan"]["trigger_event_ids"] == [event["event_id"]]


def test_legacy_humming_preview_still_echoes_unchanged_notes():
    event = source("humming")
    lead = next(part.notes for part in read_midi(render_clip(event)).instruments if part.name == "lead")
    notes = event["payload"]["melody"]["notes"]
    assert [note.pitch for note in lead] == [note["pitchMidi"] for note in notes]
    assert [note.start for note in lead] == pytest.approx([note["startSec"] for note in notes], abs=.003)
