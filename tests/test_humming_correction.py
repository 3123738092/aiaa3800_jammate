import json
from contextlib import closing

import pytest

from jammate.analysis.humming_correction import correct_humming
from jammate.browser_jam import BrowserJamService
from jammate.orchestration.llm import ModelReply, ProviderFailure


def phrase():
    pitches = [
        (67, 0., .55, .90, .82),
        (66, .55, .15, .22, .55),
        (67, .70, .40, .10, .76),
        (82, 1.20, .11, .18, .35),
        (69, 1.40, .50, .90, .84),
        (70, 1.90, .12, .22, .51),
    ]
    notes = [
        {"id": f"h{i}", "pitchMidi": p, "startSec": start, "durationSec": duration, "velocity01": .7,
         "onsetActivation": onset, "modelActivation": activation, "sourceNoteIds": [f"n{i}"],
         "frameRange": [round(100 * start), round(100 * (start + duration))]}
        for i, (p, start, duration, onset, activation) in enumerate(pitches, 1)
    ]
    raw = [{key: value for key, value in note.items() if key != "frameRange"} |
           {"id": note["sourceNoteIds"][0]} for note in notes]
    raw[4]["durationSec"] = .64
    return {
        "schemaVersion": "1.0.0", "id": "humming", "status": "ready",
        "source": {"id": "humming", "durationSec": 2.4},
        "timebase": {"unit": "seconds", "origin": "original_audio_start"},
        "tempo": {"bpm": None, "source": "unknown", "mode": "free", "quantized": False},
        "key": None, "timeSignature": None, "selectedResult": "cleaned", "notes": notes,
        "results": {"raw": {"notes": raw}, "cleaned": {"notes": notes, "forceMonophonic": True}},
    }


class Model:
    ready = True

    def complete_humming(self, messages, schema, timeout_s):
        context = json.loads(messages[1]["content"])
        candidates = context["candidates"]
        assert [candidate["action"] for candidate in candidates.values()] == ["merge", "drop", "retune"]
        assert [note["pitchMidi"] for note in context["notes"]] == [67, 66, 67, 82, 69, 70]
        assert timeout_s <= 8.
        return ModelReply(json.dumps({"edits": [
            {"candidate_id": identity, "reason": "有转写误差的迹象"} for identity in candidates
        ]}), "melody-test", {})


def test_opening_and_live_capture_use_corrected_notes_and_keep_evidence():
    document = phrase()
    with closing(BrowserJamService(audio_router=Model())) as service:
        assert service.dispatch("status", {})["humming_correction_ready"]
        view = service.dispatch("start", {"opening_mode": "cold",
            "opening": {"humming": {"melody": document, "correct_humming": True}}})
        session = view["session"]
        debug = service.dispatch("debug", {"session": session})
        opening = debug["opening"]["input_events"][0]["payload"]["melody"]
        assert [note["pitch"] for note in debug["jam_state"]["motifs"]["last_user"]] == [67, 69, 69]
        request = {"session": session, "mode": "humming", "melody": document}
        unchanged = service.dispatch("capture", request)["event"]["payload"]["melody"]
        assert unchanged["notes"] == document["notes"] and "llmCorrection" not in unchanged
        for mode in ("humming", "auto"):
            live = service.dispatch("capture", {**request, "mode": mode, "correct_humming": True})
            corrected = live["event"]["payload"]["melody"]
            assert corrected["notes"] == opening["notes"]
            assert corrected["results"]["raw"] == document["results"]["raw"]
            assert corrected["llmCorrection"]["before_notes"] == document["notes"]
            assert corrected["llmCorrection"]["model"] == "melody-test"
            assert [note["pitchMidi"] for note in corrected["notes"]] == [67, 69, 69]
            assert corrected["notes"][0]["durationSec"] == pytest.approx(1.10)
            assert corrected["notes"][-1]["startSec"] == 1.90


def test_clear_attacks_and_rests_are_not_offered_for_merging():
    document = phrase()
    document["results"]["cleaned"]["acousticAttacks"] = {"h2": True, "h4": True}
    document["notes"][5]["onsetActivation"] = .95

    def unexpected(*args):
        pytest.fail("a clearly articulated phrase should not require a model call")

    result = correct_humming(document, unexpected)
    assert result["notes"] == document["notes"]
    assert result["llmCorrection"]["reason"] == "no_candidates"

    document["results"]["cleaned"]["acousticAttacks"]["h2"] = False
    document["notes"][1]["startSec"] = .61
    document["notes"][1]["durationSec"] = .09
    assert correct_humming(document, unexpected)["llmCorrection"]["reason"] == "no_candidates"


def test_detector_onset_can_still_be_drift_without_acoustic_reattack():
    document = phrase()
    document["notes"][1]["onsetActivation"] = .75
    document["notes"][2]["onsetActivation"] = .70
    document["results"]["cleaned"]["acousticAttacks"] = {"h2": False, "h3": False}

    def complete(messages, schema, timeout_s):
        first = json.loads(messages[1]["content"])["candidates"]["c1"]
        assert first["action"] == "merge"
        return ModelReply('{"edits":[{"candidate_id":"c1","reason":"延音内滑动"}]}', "test", {})

    result = correct_humming(document, complete)
    assert result["llmCorrection"]["status"] == "applied"
    assert result["notes"][0]["durationSec"] == pytest.approx(1.10)


def test_weak_long_note_is_reviewable_but_raw_same_pitch_support_protects_it():
    document = phrase()
    weak = document["notes"][3]
    weak.update(startSec=1.15, durationSec=.70, modelActivation=.17)
    document["results"]["cleaned"]["acousticAttacks"] = {weak["id"]: False}
    reports = []

    def complete(messages, schema, timeout_s):
        candidates = json.loads(messages[1]["content"])["candidates"]
        reports.append([candidate for candidate in candidates.values() if candidate["note_ids"] == [weak["id"]]])
        edits = [{"candidate_id": identity, "reason": "背景杂音"}
                 for identity, candidate in candidates.items() if candidate["note_ids"] == [weak["id"]]]
        return ModelReply(json.dumps({"edits": edits}), "test", {})

    corrected = correct_humming(document, complete)
    assert reports[0] and reports[0][0]["evidence"]["same_pitch_raw_fraction"] < .25
    assert weak not in corrected["notes"]
    assert weak in corrected["llmCorrection"]["before_notes"]

    document["results"]["raw"]["notes"][3].update(startSec=1.15, durationSec=.68)
    retained = correct_humming(document, complete)
    assert reports[1] == [] and retained["notes"] == document["notes"]


@pytest.mark.parametrize("reply,reason", [
    ('{"edits":[{"candidate_id":"c999","reason":"invented"}]}', "invalid_reply"),
    ('{"edits":[{"candidate_id":"c1","reason":"a"},{"candidate_id":"c1","reason":"b"}]}', "invalid_reply"),
    (None, "timeout"),
])
def test_failure_preserves_the_base_phrase(reply, reason):
    document = phrase()

    def complete(*args):
        if reply is None:
            raise ProviderFailure("timeout")
        return ModelReply(reply, "test", {})

    result = correct_humming(document, complete)
    assert result["llmCorrection"]["status"] == "fallback"
    assert result["llmCorrection"]["reason"] == reason
    assert result["notes"] == document["notes"]
