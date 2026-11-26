import base64
import io
from contextlib import closing

import numpy as np
import pytest
import soundfile as sf

from jammate.browser_jam import BrowserJamService


def performance(onsets=(219, 733, 1461, 1730, 2472)):
    pitches = (60, 64, 67, 62, 65)
    lengths = (208, 301, 138, 441, 200)
    velocities = (91, 80, 103, 74, 99)
    events = []
    for start, pitch, length, velocity in zip(onsets, pitches, lengths, velocities):
        events.extend([
            dict(kind="note_on", timestampMs=start, note=pitch, velocity=velocity, channel=2),
            dict(kind="note_off", timestampMs=start + length, note=pitch, velocity=0, channel=2),
        ])
    return {"events": sorted(events, key=lambda event: event["timestampMs"]),
            "origin": "keyboard", "source_bpm": 122, "target": "keys"}


@pytest.mark.parametrize("meter,beats", [("4/4", 4), ("3/4", 3), ("6/8", 2)])
def test_human_timed_midi_reaches_live_score_and_wav_at_session_tempo(meter, beats):
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening": {"midi": performance()}, "opening_mode": "cold", "bpm": 102, "meter": meter,
        })
        session = service.sessions[view["session"]]
        opening = session.debug()["opening"]
        event, = opening["input_events"]
        assert event["source"] == "midi" and event["provenance"]["target"] == "keys"
        document = event["payload"]["melody"]
        assert document["source"]["sourceBpm"] == 122
        assert document["tempo"]["quantized"] is False
        assert [note["startSec"] for note in document["notes"]] == pytest.approx([0, .514, 1.242, 1.511, 2.253])
        assert [note["durationSec"] for note in document["notes"]] == pytest.approx([.208, .301, .138, .441, .2])
        assert [note["velocity01"] * 127 for note in document["notes"]] == pytest.approx([91, 80, 103, 74, 99])
        assert view["meter"] == meter and view["bpm"] == 102
        assert view["key"] is None and view["chord"] is None and view["progression"] is None

        pitches = {60, 64, 67, 62, 65}
        actual = {}
        packet = view["next"]
        for _ in range(3):
            bar = packet["bar"]
            after = session.poll(bar, packet["start"], bar)
            assert after["current_bar"] == bar
            for note in session.debug()["current"]["notes"]:
                if note["instrument"] == "lead" and note["pitch"] in pitches:
                    actual.setdefault(note["pitch"], note["start"] + beats * bar)
            if bar == 0:
                audio, rate = sf.read(io.BytesIO(base64.b64decode(packet["wav"])))
                assert packet["end"] / rate == pytest.approx(beats * 60 / 102, abs=1 / rate)
                assert np.max(np.abs(audio)) > .001
            packet = after["next"] or session.poll(bar, packet["end"] - 1, bar)["next"]
        assert list(actual) == [60, 64, 67, 62, 65]
        assert list(actual.values()) == pytest.approx(np.array([0, .514, 1.242, 1.511, 2.253]) * 102 / 60)


def test_studio_batches_live_midi_and_text_in_same_decision():
    with closing(BrowserJamService()) as service:
        opened = service.dispatch("start", {
            "opening": {"text": "完整乐队伴奏", "target": "band"}, "opening_mode": "cold", "bpm": 96, "meter": "4/4",
        })
        session = service.sessions[opened["session"]]
        first = opened["next"]
        session.poll(0, 0, 0)
        result = service.dispatch("studio-input", {"session": session.id, "input": {
            "target": "drums", "text": "少一点", "midi": performance(),
        }})
        event_id = result["feedback"]["id"]
        session.future.result(timeout=3)
        after = session.poll(0, first["end"] - 1, 0)
        turn, = session.debug()["turns"]
        ids = [event["event_id"] for event in turn["input_events"]]
        assert ids[0] == event_id and len(ids) == 2
        assert turn["input_events"][0]["provenance"]["original_text"] == "少一点"
        assert set(turn["bound_plan"]["trigger_event_ids"]) == set(ids)
        assert after["feedback"]["status"] == "scheduled"
        assert "playback_receipt" not in turn
        packet = after["next"]
        applied = session.poll(packet["bar"], packet["start"], packet["bar"])
        assert applied["feedback"]["status"] == "applied"
        assert session.debug()["turns"][0]["playback_receipt"]["bar"] == packet["bar"]


def test_studio_harmony_readout_waits_for_applied_bar():
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening": {"text": "完整乐队伴奏", "target": "band"}, "opening_mode": "cold", "bpm": 96, "meter": "4/4",
        })
        session = service.sessions[view["session"]]
        first = view["next"]
        assert view["chord"] is None and view["progression_index"] is None
        session.poll(0, 0, 0)
        service.dispatch("studio-input", {"session": session.id, "input": {
            "target": "harmony", "text": "把第二个和弦换成 Am7",
        }})
        session.future.result(timeout=3)
        scheduled = session.poll(0, first["end"] - 1, 0)
        assert scheduled["feedback"]["status"] == "scheduled"
        assert scheduled["progression"][1] == "G"
        packet = scheduled["next"]
        applied = session.poll(packet["bar"], packet["start"], packet["bar"])
        assert applied["feedback"]["status"] == "applied"
        assert applied["progression"][1] == "Am7"
        assert applied["chord"] == applied["progression"][applied["progression_index"]]


@pytest.mark.parametrize("idea,part,meter", [
    ("bossa_68", "drums", "6/8"), ("offbeat_44", "drums", "4/4"),
    ("light_34", "drums", "3/4"), ("piano_reply", "lead", "4/4"),
])
def test_random_opening_offer_starts_only_its_short_part(monkeypatch, idea, part, meter):
    monkeypatch.setattr("jammate.browser_jam.choice", lambda _: idea)
    with closing(BrowserJamService()) as service:
        offer = service.dispatch("studio-suggestion", {})["offer"]
        assert not service.sessions and offer["id"] == idea
        view = service.dispatch("start", {"studio_idea": offer["id"]})
        session = service.sessions[view["session"]]
        assert view["meter"] == meter
        assert [layer["name"] for layer in view["layers"] if layer["active"]] == [part]
        packet = view["next"]
        session.poll(0, packet["start"], 0)
        debug = session.debug()
        assert debug["opening"]["suggestion"]["id"] == idea
        assert {note["instrument"] for note in debug["current"]["notes"]} == {part}
        if part == "drums":
            assert debug["key"] is None and debug["progression"] == []
        else:
            assert debug["opening"]["solo_plan"]["valid_for_bars"] == 2


def test_live_initiative_is_only_staged_after_acceptance_and_plays_short_motif():
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {"opening": {"text": "完整乐队伴奏"}, "opening_mode": "cold"})
        session = service.sessions[view["session"]]
        assert session.offer_suggestion() is None
        first = view["next"]
        session.poll(0, first["start"], 0)
        version = session.manager.state.state_version
        offer = session.offer_suggestion()
        assert offer and not session.debug()["turns"]
        assert session.manager.state.state_version == version and not session.manager.state.layer("lead").active
        assert session.offer_suggestion() == offer
        with pytest.raises(ValueError, match="已过期"):
            session.accept_suggestion("another-offer")
        session.accept_suggestion(offer["id"])
        session.future.result(timeout=3)
        scheduled = session.poll(0, first["end"] - 1, 0)
        assert scheduled["feedback"]["status"] == "scheduled"
        assert not session.manager.state.layer("lead").active
        assert session.accept_suggestion(offer["id"]) == scheduled["feedback"]
        turn, = session.debug()["turns"]
        assert turn["input_event"]["payload"]["command"] == "accept_suggestion"
        assert turn["bound_plan"]["strategy"] == "INITIATE"
        assert turn["suggestion"]["id"] == offer["id"]
        packet = scheduled["next"]
        applied = session.poll(packet["bar"], packet["start"], packet["bar"])
        assert applied["feedback"]["status"] == "applied"
        assert any(note["instrument"] == "lead" for note in session.debug()["current"]["notes"])


def test_an_explicitly_accepted_second_motif_is_not_an_idle_only_turn():
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {"studio_idea": "piano_reply"})
        session = service.sessions[view["session"]]
        packet = view["next"]
        session.poll(0, packet["start"], 0)
        assert session.manager.state.interaction.awaiting_user
        offer = session.offer_suggestion()
        session.accept_suggestion(offer["id"])
        for bar in range(1, 5):
            if session.future:
                session.future.result(timeout=3)
            packet = session.snapshot(buffered=bar - 1)["next"] or session.poll(bar - 1, packet["end"] - 1, bar - 1)["next"]
            after = session.poll(bar, packet["start"], bar)
            if after["feedback"]["status"] == "applied":
                break
        assert after["feedback"]["status"] == "applied"
