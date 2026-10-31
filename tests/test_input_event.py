import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from jammate.contracts import InputEvent, InputEventError, input_event_schema, load_input_event
from jammate.contracts.adapters import from_humming, from_rhythm, from_text, to_legacy_phrase, to_legacy_rhythm
from jammate.contracts.__main__ import main
from jammate.rhythm import RhythmContext

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "input_events"
pytestmark = pytest.mark.unit


@pytest.fixture
def melody():
    return json.loads((FIXTURES / "source" / "humming_melody_v1.json").read_text(encoding="utf-8"))


@pytest.fixture
def rhythm():
    return RhythmContext(100, (0, 1, 1.5, 3), (1, .55, .8, 1), beat_origin_s=3, confidence=.94)


def rhythm_event(rhythm, **kwargs):
    return from_rhythm(rhythm, session_id="jam_test", event_id="evt_rhythm", audio_duration_s=6,
                       tempo_source="session", started_at=10, **kwargs)


def test_schema_and_shipped_examples_agree():
    schema = input_event_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for path in FIXTURES.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(data)
        assert InputEvent.from_dict(data).to_dict() == data
    assert len(list(FIXTURES.glob("*.json"))) == 3


def test_text_keeps_negation_punctuation_whitespace_and_all_clauses():
    original = "  别停！鼓收一点，刚才那句保留。\n不要加速。  "
    event = from_text(original, session_id="jam_test", event_id="evt_text", started_at=4, ended_at=4)
    assert event.payload == {"text": original}
    assert event.to_dict()["source"] == "text"
    assert event.event_type == "speech"
    assert "intent" not in event.payload
    assert "action" not in event.payload


def test_asr_candidates_are_evidence_not_commands():
    event = from_text("不要停", session_id="jam_test", source="speech", language="zh",
                      alternatives=["不要听"], audio_ref="recordings/a.wav")
    assert event.payload["text"] == "不要停"
    assert event.payload["alternatives"] == ["不要听"]
    assert event.to_dict()["confidence"] is None


def test_reloading_retry_keeps_identity_new_submission_gets_new_identity(tmp_path):
    first = from_text("same", session_id="jam_test")
    second = from_text("same", session_id="jam_test")
    assert first.event_id != second.event_id
    path = first.write(tmp_path / "event.json")
    assert load_input_event(path) == first


def test_event_snapshot_is_detached_from_nested_source_and_return_values(melody):
    before = copy.deepcopy(melody)
    event = from_humming(melody, session_id="jam_test")
    melody["notes"][0]["pitchMidi"] = 1
    returned = event.payload
    returned["melody"]["notes"].clear()
    assert event.payload["melody"] == before


def test_unknown_capture_times_remain_unknown(melody):
    event = from_humming(melody, session_id="jam_test")
    assert event.to_dict()["started_at"] is None
    assert event.to_dict()["ended_at"] is None
    assert "createdAt" in event.payload["melody"]


def test_humming_wrapper_preserves_full_evidence_and_does_not_use_midi_bpm(melody):
    before = copy.deepcopy(melody)
    event = from_humming(melody, session_id="jam_test", event_id="evt_hum", started_at=20)
    assert event.payload["melody"] == before
    assert melody == before
    assert event.to_dict()["ended_at"] == pytest.approx(20 + melody["source"]["durationSec"])
    assert event.payload["melody"]["tempo"]["bpm"] is None
    assert event.payload["melody"]["midiExport"]["referenceBpm"] == 120
    assert event.to_dict()["confidence"] is None
    assert event.to_dict()["provenance"]["source_id"] == melody["id"]


def test_legacy_phrase_keeps_leading_rest_overlaps_and_requested_session_tempo(melody):
    event = from_humming(melody, session_id="jam_test", started_at=20)
    phrase = to_legacy_phrase(event, tempo_bpm=100)
    assert len(phrase.notes) == 12
    assert phrase.notes[0].start == pytest.approx(.325079 * 100 / 60)
    assert phrase.notes[0].start == phrase.notes[1].start
    assert [note.pitch for note in phrase.notes] == [note["pitchMidi"] for note in melody["notes"]]
    assert all(note.velocity == 90 and note.instrument == "user" for note in phrase.notes)
    assert event.payload["melody"] == melody
    with pytest.raises(InputEventError, match="negative"):
        to_legacy_phrase(event, tempo_bpm=100, beat_origin_s=1)


@pytest.mark.parametrize("tempo", [None, True, 0, 200, float("nan")])
def test_legacy_phrase_requires_supported_explicit_tempo(melody, tempo):
    event = from_humming(melody, session_id="jam_test")
    with pytest.raises(InputEventError, match="tempo_bpm"):
        to_legacy_phrase(event, tempo_bpm=tempo)


def test_rhythm_time_origin_and_heuristic_do_not_get_reinterpreted(rhythm):
    event = rhythm_event(rhythm, confidence_kind="grid_alignment_heuristic")
    assert event.to_dict()["started_at"] == 10
    assert event.to_dict()["ended_at"] == 16
    payload = event.payload
    assert payload["rhythm"]["beat_origin_s"] == 3
    assert payload["rhythm"]["confidence"] == .94
    assert event.to_dict()["confidence"] is None
    # First bar begins at session second 13; its leading count-in was not subtracted twice.
    assert event.to_dict()["started_at"] + payload["rhythm"]["beat_origin_s"] == 13
    legacy = to_legacy_rhythm(event)
    assert legacy.context == rhythm
    assert legacy.update_tempo is False
    assert legacy.mode is None
    detected = from_rhythm(rhythm, session_id="jam_test", audio_duration_s=6, tempo_source="detected")
    assert to_legacy_rhythm(detected).update_tempo is True


def test_empty_perception_is_valid_without_fabricating_material(melody):
    for result in melody["results"].values():
        result["notes"] = []
    melody["notes"] = []
    melody["status"] = "no_notes"
    event = from_humming(melody, session_id="jam_test")
    assert to_legacy_phrase(event, tempo_bpm=100).notes == ()
    context = RhythmContext(100, (), (), confidence=0)
    event = from_rhythm(context, session_id="jam_test", audio_duration_s=2.4, tempo_source="manual")
    assert event.payload["status"] == "no_onsets"


@pytest.mark.parametrize("field,value", [
    ("schema_version", "0.1-draft"), ("event_id", " "), ("session_id", ""),
    ("started_at", -1), ("started_at", True), ("started_at", float("inf")),
    ("confidence", float("nan")), ("confidence", 1.1), ("confidence", True),
    ("source", "rhythm"), ("event_type", "session_control"),
])
def test_invalid_envelope_is_rejected(field, value):
    data = from_text("hello", session_id="jam_test").to_dict()
    data[field] = value
    with pytest.raises(InputEventError):
        InputEvent.from_dict(data)


def test_closed_envelope_and_payload_reject_accidental_actions():
    data = from_text("hello", session_id="jam_test").to_dict()
    data["payload"]["action"] = "BUILD"
    with pytest.raises(InputEventError, match="Additional properties"):
        InputEvent.from_dict(data)
    data["payload"].pop("action")
    data["intent"] = "BUILD"
    with pytest.raises(InputEventError, match="Additional properties"):
        InputEvent.from_dict(data)


@pytest.mark.parametrize("start,end", [(0, None), (None, 2), (3, 2)])
def test_capture_clock_requires_consistent_pair(start, end):
    with pytest.raises(InputEventError):
        from_text("hello", session_id="jam_test", started_at=start, ended_at=end)


def test_event_score_needs_an_explicit_method():
    data = from_text("hello", session_id="jam_test").to_dict()
    data["confidence"] = .8
    with pytest.raises(InputEventError, match="confidence_method"):
        InputEvent.from_dict(data)
    data["provenance"]["confidence_method"] = "producer_defined_example"
    assert InputEvent.from_dict(data).to_dict()["confidence"] == .8


@pytest.mark.parametrize("mutation", [
    lambda p: p["rhythm"].update(onset_beats=[1, 0, 1.5, 3]),
    lambda p: p["rhythm"].update(accents=[1]),
    lambda p: p["rhythm"].update(onset_beats=[0, 1, 1, 3]),
    lambda p: p["rhythm"].update(onset_beats=[0, 1, 1.5, 4]),
    lambda p: p["rhythm"].update(beat_origin_s=5),
    lambda p: p.update(status="no_onsets"),
])
def test_rhythm_cross_field_validation(rhythm, mutation):
    data = rhythm_event(rhythm).to_dict()
    mutation(data["payload"])
    with pytest.raises(InputEventError):
        InputEvent.from_dict(data)


@pytest.mark.parametrize("mutation", [
    lambda m: m["notes"].pop(),
    lambda m: m.update(selectedResult="missing"),
    lambda m: m.update(timebase={"unit": "beats", "origin": "first_note"}),
    lambda m: m["tempo"].update(bpm=120),
    lambda m: m.update(status="no_notes"),
    lambda m: m["source"].update(durationSec=.1),
    lambda m: m["results"]["raw"]["notes"][0].update(startSec=-.1),
    lambda m: m["results"]["raw"]["notes"][0].update(pitchMidi=True),
    lambda m: m["results"]["raw"]["notes"][0].update(sourceNoteIds=["missing"]),
    lambda m: m["results"]["raw"]["notes"].reverse(),
])
def test_humming_contract_rejects_corruption(melody, mutation):
    mutation(melody)
    with pytest.raises(InputEventError):
        from_humming(melody, session_id="jam_test")


def test_audio_clock_mapping_cannot_silently_change_duration(melody):
    data = from_humming(melody, session_id="jam_test", started_at=20).to_dict()
    data["ended_at"] += .1
    with pytest.raises(InputEventError, match="interval"):
        InputEvent.from_dict(data)


def test_duplicate_json_keys_and_nonfinite_metadata_are_rejected(tmp_path, melody):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":"1.0.0","schema_version":"2.0.0"}')
    with pytest.raises(InputEventError, match="duplicate JSON key"):
        load_input_event(path)
    melody["extra_evidence"] = {"score": float("nan")}
    with pytest.raises(InputEventError, match="extra_evidence.score"):
        from_humming(melody, session_id="jam_test")


def test_cli_handoff_file_to_event_and_validation(tmp_path, capsys):
    output = tmp_path / "humming.event.json"
    assert main(["wrap-humming", str(FIXTURES / "source" / "humming_melody_v1.json"),
                 "--session-id", "jam_cli", "--event-id", "evt_cli", "--started-at", "20", "--out", str(output)]) == 0
    assert load_input_event(output).event_id == "evt_cli"
    assert main(["validate", str(output)]) == 0
    assert '"valid": true' in capsys.readouterr().out
    output.write_text('{"bad":true}')
    assert main(["validate", str(output)]) == 2
    assert "invalid input" in capsys.readouterr().err


def test_cli_rhythm_and_text_preserve_existing_data(tmp_path):
    output = tmp_path / "rhythm.event.json"
    assert main(["wrap-rhythm", str(ROOT / "examples" / "rhythm_a.json"), "--session-id", "jam_cli",
                 "--audio-duration", "2.4", "--tempo-source", "manual", "--out", str(output)]) == 0
    assert load_input_event(output).payload["rhythm"]["onset_beats"] == [0, 1, 1.5, 3]
    message = "别停，鼓收一点"
    assert main(["text", message, "--session-id", "jam_cli", "--out", str(output)]) == 0
    assert load_input_event(output).payload["text"] == message
