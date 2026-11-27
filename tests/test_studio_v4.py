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


def test_pad_recording_preserves_drum_sounds_and_uses_the_session_tempo():
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening": {"rhythm": {"bpm": 100, "meter": "3/4", "hits": [
                {"pad": 0, "timestampMs": 0}, {"pad": 2, "timestampMs": 0},
                {"pad": 5, "timestampMs": 750}, {"pad": 3, "timestampMs": 1100},
            ]}},
            "opening_mode": "cold", "bpm": 120, "meter": "3/4",
        })
        session = service.sessions[view["session"]]
        debug = session.debug()
        event, = debug["opening"]["input_events"]
        assert event["source"] == "rhythm" and event["event_type"] == "rhythm_seed"
        assert event["provenance"]["producer"] == "studio_drum_pads"
        rhythm = event["payload"]["rhythm"]
        assert rhythm["drum_pitches"] == [[36, 42], [38], [75]]
        assert rhythm["onset_beats"] == pytest.approx((0, 1.25, 11 / 6))
        assert [n["pitch"] for n in debug["queued"]["notes"]] == [36, 42, 38, 75]
        assert [n["start"] for n in debug["queued"]["notes"]] == pytest.approx([0, 0, 1.25, 11 / 6])
        packet = view["next"]
        audio, rate = sf.read(io.BytesIO(base64.b64decode(packet["wav"])))
        assert (packet["end"] - packet["start"]) / rate == pytest.approx(1.5, abs=1 / rate)
        assert len(audio) >= packet["end"] - packet["start"]
        assert np.max(np.abs(audio)) > .001
        session.poll(packet["bar"], packet["start"], packet["bar"])
        assert session.debug()["opening"]["playback_receipt"]["bar"] == 0


@pytest.mark.parametrize("bpm, count_in, expected_source", [
    (84, [], "session"),
    (120, [0, 500, 1000, 1500], "detected"),
])
def test_pad_tempo_mode_keeps_all_hits_in_the_opening_event(bpm, count_in, expected_source):
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening": {"rhythm": {"bpm": bpm, "meter": "4/4", "count_in_ms": count_in, "hits": [
                {"pad": 0, "timestampMs": 0}, {"pad": 1, "timestampMs": 500},
                {"pad": 3, "timestampMs": 3100},
            ]}},
            "opening_mode": "cold", "meter": "4/4",
        })
        session = service.sessions[view["session"]]
        event, = session.debug()["opening"]["input_events"]
        payload = event["payload"]
        assert view["bpm"] == bpm
        assert payload["tempo_source"] == expected_source
        assert payload["rhythm"]["onset_beats"] == pytest.approx([0, bpm / 120])
        assert payload["rhythm"]["drum_pitches"] == [[36], [38]]
        assert payload["pad_recording"] == {
            "count_in_ms": count_in,
            "hits": [
                {"pad": 0, "pitch": 36, "timestamp_ms": 0},
                {"pad": 1, "pitch": 38, "timestamp_ms": 500},
                {"pad": 3, "pitch": 75, "timestamp_ms": 3100},
            ],
        }


def test_live_pad_recording_replaces_groove_and_enables_drums_in_a_cold_melody_session():
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening": {"midi": performance()}, "opening_mode": "cold", "bpm": 96, "meter": "4/4",
        })
        session_id = view["session"]
        session = service.sessions[session_id]
        packet = view["next"]
        session.poll(packet["bar"], packet["start"], packet["bar"])
        assert not session.manager.state.layer("drums").active
        take = {"bpm": 96, "meter": "4/4", "hits": [{"pad": 3, "timestampMs": 0}]}
        result = service.dispatch("studio-input", {"session": session_id, "input": {"rhythm": take}})
        assert result["feedback"]["text"] == "鼓垫节奏"
        event_id = result["feedback"]["id"]
        session.future.result(timeout=3)
        updated = session.poll(0, packet["end"] - 1, 0)["next"]
        session.poll(updated["bar"], updated["start"], updated["bar"])
        debug = session.debug()
        assert debug["turns"][0]["input_event"]["event_id"] == event_id
        assert debug["turns"][0]["playback_receipt"]["bar"] == updated["bar"]
        assert [n["pitch"] for n in debug["current"]["notes"] if n["instrument"] == "drums"] == [75]
        assert session.manager.state.tempo_bpm == 96


def test_pad_stream_conditions_use_the_recorded_meter_after_the_first_bar():
    from jammate.music import Note
    from jammate.orchestration.realtime import PlaybackBar
    from jammate.render.clock import BarTiming
    from jammate.state import JamState
    from mrt2_demo.browser_stream import bar_conditions

    state = JamState(tempo_bpm=120, beats_per_bar=3)
    bar = PlaybackBar(BarTiming(2, 144000, 216000, 120),
                      (Note(38, 7., .1, 90, "drums"),), state=state)
    _, drums = bar_conditions(bar, 38, 0)
    assert np.flatnonzero(drums[:, 0]).tolist() == [12, 13]


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
