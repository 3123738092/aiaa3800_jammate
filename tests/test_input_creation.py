"""Check what the listener actually gets back from one input."""
import base64
import io
import json
from pathlib import Path

import pretty_midi
import pytest

from jammate.contracts import InputEvent
from jammate.input_band import render_clip
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.manager import StateManager
from jammate.state import JamState
from renderer_experiments.app.band_plan import KevinRuntime


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


def test_fully_occupied_rhythm_changes_instrumentation_without_moving_anchors():
    event = source("rhythm")
    beats = [index / 4 for index in range(16)]
    event["payload"]["rhythm"].update(
        tempo_bpm=100, onset_beats=beats, accents=[1] + [.5] * 15, confidence=1,
    )
    legacy = next(part.notes for part in read_midi(render_clip(event)).instruments if part.is_drum)
    creative = next(part.notes for part in read_midi(render_clip(event, creative=True)).instruments if part.is_drum)
    assert {note.start for note in creative} == {note.start for note in legacy}
    assert {(note.start, note.pitch) for note in creative} != {(note.start, note.pitch) for note in legacy}


def test_legacy_humming_preview_still_echoes_unchanged_notes():
    event = source("humming")
    lead = next(part.notes for part in read_midi(render_clip(event)).instruments if part.name == "lead")
    notes = event["payload"]["melody"]["notes"]
    assert [note.pitch for note in lead] == [note["pitchMidi"] for note in notes]
    assert [note.start for note in lead] == pytest.approx([note["startSec"] for note in notes], abs=.003)


@pytest.mark.parametrize(("text", "tempo"), [("来一段，慢一点", 92), ("来一段，快一点", 108)])
def test_text_starts_a_new_passage_at_the_requested_relative_speed(text, tempo):
    result = render_clip({"text": text}, creative=True)
    midi = read_midi(result)
    assert midi.get_tempo_changes()[1][0] == pytest.approx(tempo, abs=.001)
    assert next(part.notes for part in midi.instruments if part.name == "lead")
    assert result["plan"]["trigger_event_ids"] == [result["event_id"]]


def test_unsupported_text_is_not_rendered_as_a_generic_song():
    with pytest.raises(ValueError, match="指令未执行"):
        render_clip({"text": "来一段，变成一只猫"}, creative=True)


def test_overlapping_raw_pitches_that_converge_in_a_variation_export_cleanly():
    document = source("humming")
    melody = document["payload"]["melody"]
    raw = melody["results"]["raw"]["notes"][:2]
    for note, pitch, onset in zip(raw, (60, 61), (.3, .5), strict=True):
        note.update(pitchMidi=pitch, startSec=onset, durationSec=.7, sourceNoteIds=[note["id"]])
    melody["results"]["raw"]["notes"] = raw
    melody["results"]["cleaned"].update(notes=raw[:1], forceMonophonic=False)
    melody.update(selectedResult="raw", notes=raw)
    event = InputEvent.from_dict(document)
    manager = StateManager(JamState(session_id=event.session_id, tempo_bpm=100))
    accepted = manager.ingest([event])
    state, history = manager.decision_snapshot()
    plan = RuleAgent().decide(accepted, state, plan_id="overlap_0", history=history, vary_input=True)
    runtime = KevinRuntime(Path("unused"), executable=Path("unused"), soundfont=Path("unused"))

    variation = runtime.validate(state, plan)

    assert variation.lead_notes
    assert max(note.end for note in variation.lead_notes) == pytest.approx(1.5)
    assert [note.pitch for note in state.motifs[0].notes] == [60, 61]
