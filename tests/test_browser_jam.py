from concurrent.futures import ThreadPoolExecutor
from threading import Event

from jammate.browser_jam import BrowserJam, BrowserJamService, voice_command
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import ProviderFailure
from jammate.orchestration.tempo_intent import tempo_clause
from jammate.state import JamState


def test_browser_confirms_the_audio_bar_before_committing_feedback():
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers)
        jam.reserve()
        zero = jam.snapshot()["next"]
        assert jam.snapshot()["current_bar"] is None
        jam.poll(0, 0, 0)
        jam.submit("鼓少一点")
        jam.future.result(timeout=2)
        buffered = jam.poll(0, zero["end"] - 1000, 0)
        debug = jam.debug()
        (turn,) = debug["turns"]

        assert buffered["feedback"]["status"] == "scheduled"
        assert debug["key"] == "C major" and debug["chord"] == "C"
        assert debug["progression"] == ["C", "G", "Am", "F"]
        assert debug["queued"]["bar_index"] == 1
        assert turn["input_event"]["payload"]["text"] == "鼓少一点"
        assert turn["decision_state"]["arrangement"]["layers"][0]["density"] == 1
        assert turn["bound_plan"]["effective_at"]["bar"] == 1
        assert "playback_receipt" not in turn
        assert buffered["next"]["start"] == zero["end"]
        assert jam.manager.state.layer("drums").density == 1
        assert jam.snapshot(buffered=1)["next"] is None

        applied = jam.poll(1, zero["end"], 1)
        debug = jam.debug()
        (turn,) = debug["turns"]
        drums, bass, keys = applied["layers"]
        assert applied["feedback"]["status"] == "applied"
        assert applied["last_applied"]["bar"] == 1 and applied["bpm"] == 96
        assert drums["density"] < 1 and bass["density"] == keys["density"] == 1
        assert len(debug["current"]["notes"]) == len(jam.playing.notes) > 0
        assert all(0 <= note["start"] < 4 for note in debug["current"]["notes"])
        assert [note["pitch"] for note in debug["current"]["notes"]] == [note.pitch for note in jam.playing.notes]
        assert turn["playback_receipt"]["bar"] == 1
        assert turn["state_after_playback"]["arrangement"]["layers"][0]["density"] == drums["density"]
        version = jam.manager.state.state_version
        jam.poll(1, zero["end"] + 10, 1)
        assert jam.manager.state.state_version == version


def test_browser_debug_keeps_model_errors_with_the_failed_input():
    class TimedOutAgent:
        last_trace = {"source": "fallback", "requested_model": "test-model",
                      "attempts": [{"status": "provider_failure", "kind": "timeout"}]}

        def decide(self, *args, **kwargs):
            raise ProviderFailure("timeout")

    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, TimedOutAgent, workers)
        jam.reserve()
        jam.poll(0, 0, 0)
        jam.submit("测试风格")
        jam.future.result(timeout=2)
        (turn,) = jam.debug()["turns"]
        assert turn["input_event"]["payload"]["text"] == "测试风格"
        assert turn["feedback"]["status"] == "held"
        assert turn["decision"]["requested_model"] == "test-model"
        assert turn["error"] == {"type": "ProviderFailure", "message": "timeout"}
        assert "playback_receipt" not in turn


def test_opening_inputs_are_optional_individually():
    from contextlib import closing
    import pytest

    with closing(BrowserJamService()) as service:
        with pytest.raises(ValueError, match="至少一种开场素材"):
            service.dispatch("start", {"opening": {"text": "   "}})
        text, = service.opening_events({"text": "92 BPM，D 大调，开反拍"})
        assert text.source == "text"
        with pytest.raises(ValueError, match="录音格式"):
            service.opening_events({"rhythm": {"audio": "clip"}})
        assert not service.sessions


def test_long_humming_clip_reaches_analysis_in_opening_and_live_capture(monkeypatch):
    import base64
    from contextlib import closing
    import io
    import wave

    import pytest

    def recording(seconds):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(bytes(seconds * 16000 * 2))
        return {"mime_type": "audio/wav", "audio": base64.b64encode(stream.getvalue()).decode()}

    durations = []

    def analyze(audio, rate, *, mode, session_id, bpm):
        assert mode == "humming"
        durations.append(len(audio) / rate)
        return None, False

    monkeypatch.setattr("jammate.browser_jam.analyze_capture", analyze)
    with closing(BrowserJamService()) as service:
        assert service.dispatch("status", {})["humming_clip_seconds"] == 20
        clip = recording(16)
        with pytest.raises(ValueError, match="没有识别到音符"):
            service.opening_events({"humming": clip})
        with pytest.raises(ValueError, match="12 秒以内"):
            service.opening_events({"rhythm": clip})
        start = service.dispatch("start", {"opening_mode": "cold", "opening": {"text": "96 BPM"}})
        session = service.sessions[start["session"]]
        assert service.capture(session, {**clip, "mode": "humming"}) == {"recognition": "no_melody"}
        with pytest.raises(ValueError, match="9 秒以内"):
            service.capture(session, {**clip, "mode": "auto"})
        with pytest.raises(ValueError, match="20 秒以内"):
            service.capture(session, {**recording(21), "mode": "humming"})
    assert durations == [16, 16]


def test_opening_plays_selected_voices_from_both_user_events():
    import json

    from jammate.contracts.adapters import from_humming, from_rhythm
    from jammate.orchestration.jam_opening import prepare_opening
    from jammate.orchestration.llm import ModelReply
    from jammate.orchestration.llm_config import LLMConfig
    from jammate.rhythm import RhythmContext

    notes = [{"id": "n1", "pitchMidi": 69, "startSec": .25,
              "durationSec": .5, "velocity01": .7, "sourceNoteIds": ["n1"]}]
    melody = from_humming({
        "schemaVersion": "1.0.0", "id": "take", "status": "ready",
        "source": {"id": "take", "durationSec": 1., "kind": "recording"},
        "timebase": {"unit": "seconds", "origin": "original_audio_start"},
        "tempo": {"bpm": None, "source": "unknown", "mode": "free", "quantized": False},
        "key": None, "timeSignature": None, "model": {"name": "test"},
        "selectedResult": "cleaned", "notes": notes,
        "results": {"raw": {"notes": notes}, "cleaned": {"notes": notes}},
    }, session_id="opening")
    events = (from_rhythm(RhythmContext(112, (0., .75, 2., 3.25), (1., .7, 1., .7)),
                          session_id="opening", audio_duration_s=5., tempo_source="detected"), melody)

    class OpeningModel:
        def complete(self, messages, schema, timeout):
            content = json.loads(messages[1]["content"])
            assert content["tempo_bpm"] == 112
            assert content["rhythm"]["onset_beats"] == [0, .75, 2, 3.25]
            assert content["humming"][0]["pitch"] == 69
            return ModelReply(json.dumps({"tempo_bpm": 112, "rhythm_steps": [0, 3, 8, 13],
                                          "tonic": 9, "mode": "minor", "chords": [
                {"offset": 0, "quality": "min"}, {"offset": 5, "quality": "min"},
            ], "instruments": ["flute", "harp", "lead"], "reason": "A minor"}), "fixture-model", {})

    state, selection = prepare_opening(events, LLMConfig("test"), {}, provider=OpeningModel())
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(state.tempo_bpm, RuleAgent, workers, opening=(state, events, selection))
        jam.reserve()
        debug = jam.debug()
        bar = debug["queued"]
        assert bar["tempo_bpm"] == 112 and debug["key"] == "A minor"
        assert [event["source"] for event in debug["opening"]["input_events"]] == ["rhythm", "humming"]
        assert debug["opening"]["model"] == "fixture-model"
        assert {note["instrument"] for note in bar["notes"]} == {"flute", "harp", "lead"}
        assert {note["start"] for note in bar["notes"] if note["instrument"] == "flute"} == {0., .75, 2., 3.25}
        assert [(note["pitch"], note["start"]) for note in bar["notes"] if note["instrument"] == "lead"] == [(69, .25 * 112 / 60)]
        assert jam.manager.state.current_motif_id is None
        jam.poll(0, 0, 0)
        assert jam.debug()["current"] == bar
        assert jam.manager.state.current_motif_id == f"motif_{melody.event_id}"
        assert jam.debug()["opening"]["playback_receipt"]["bar"] == 0

    for supplied, voices in ((events[:1], {"drums"}), (events[1:], {"lead"}),
                             (events, {"drums", "lead"})):
        state, selection = prepare_opening(supplied, None, {}, mode="cold")
        assert JamState.from_dict(state.to_dict()) == state
        assert selection["model"] is None
        with ThreadPoolExecutor(1) as workers:
            jam = BrowserJam(state.tempo_bpm, RuleAgent, workers, opening=(state, supplied, selection))
            jam.reserve()
            debug = jam.debug()
            assert debug["key"] is None and debug["progression"] == []
            assert {note["instrument"] for note in debug["queued"]["notes"]} == voices
            for note in debug["queued"]["notes"]:
                if note["instrument"] == "lead":
                    assert note["pitch"] == 69
                    assert note["start"] == .25 * state.tempo_bpm / 60
            jam.close()


def test_cold_mrt2_opening_sends_only_drum_frames_and_keeps_realtime_audio(monkeypatch):
    def generate(style, *, output, duration, conditions, backend, model):
        notes, drums = conditions
        assert "Solo drums only" in style
        assert duration == 5
        assert not notes.any()
        assert drums[:, 0].nonzero()[0].tolist() == [0, 8, 16, 31, 39, 47, 62, 70, 78, 94, 102, 109]
        output.write_bytes(b"test-preview")
        return {"inference": "fixture"}

    monkeypatch.setattr("jammate.browser_jam.generate_isolated", generate)
    monkeypatch.setattr(BrowserJamService, "mrt2_available", staticmethod(lambda: True))
    service = BrowserJamService()
    try:
        result = service.dispatch("start", {
            "opening_mode": "cold", "renderer": "mrt2", "allow_preview": True, "bpm": 96,
            "opening": {"text": "每小节在第 1、1.5、2、3、3.5、4 拍起音"},
        })
        session = {"session": result["session"]}
        jam = service.sessions[session["session"]]
        assert result["next"]["wav"]
        jam.opening_preview_future.result(timeout=3)
        debug = service.dispatch("debug", session)
        preview = debug["opening"]["mrt2_preview"]
        assert preview["status"] == "completed"
        assert not preview["state_committed"]
        assert preview["conditioning"]["pitch_spans"] == []
        assert {note["instrument"] for note in debug["queued"]["notes"]} == {"drums"}
        assert service.dispatch("preview", {**session, "id": f"opening_{jam.id}"})["wav"]
    finally:
        service.close()


def test_mrt2_live_turns_commit_key_and_publish_only_the_current_audio(monkeypatch):
    from jammate.contracts.adapters import from_text
    from jammate.orchestration.jam_opening import prepare_opening

    rendering_key, release_key = Event(), Event()

    def preview(self, bound, event_id, arranger):
        if bound.plan.to_dict()["actions"][0]["action"] == "SET_KEY":
            rendering_key.set()
            assert release_key.wait(4)
        return {"id": event_id, "wav": "fixture"}

    monkeypatch.setattr(BrowserJam, "render_preview", preview)
    event = from_text("每小节在第 1、3 拍起音", session_id="mrt2-live")
    state, selection = prepare_opening((event,), None, {}, mode="cold")
    with ThreadPoolExecutor(1) as workers, ThreadPoolExecutor(1) as previews:
        jam = BrowserJam(state.tempo_bpm, RuleAgent, workers, renderer="mrt2",
                         opening=(state, (event,), selection), preview_workers=previews)

        def start_reserved_bar():
            current = jam.snapshot()["next"]
            jam.poll(current["bar"], current["start"], current["bar"])

        def advance_to_plan():
            start_reserved_bar()  # A buffered bar starts and releases the command.
            jam.future.result(timeout=2)
            target = jam.snapshot()["feedback"]["bar"]
            while jam.playing.timing.bar_index < target:
                start_reserved_bar()

        jam.reserve()
        start_reserved_bar()
        key_turn = jam.submit("换成 D 大调")
        advance_to_plan()
        assert rendering_key.wait(2)
        assert jam.debug()["key"] == "D major"
        assert jam.snapshot()["feedback"]["status"] == "applied"
        assert jam.snapshot()["preview_status"]["status"] == "generating"

        guitar_turn = jam.submit("加入吉他")
        assert jam.snapshot()["auto_preview_id"] is None
        release_key.set()
        advance_to_plan()
        jam.preview_future.result(timeout=2)
        assert jam.manager.state.layer("guitar").active
        assert jam.debug()["turns"][0]["decision_state"]["harmony"]["key"]["tonic"] == 2
        assert jam.snapshot()["auto_preview_id"] == guitar_turn["id"] != key_turn["id"]
        jam.close()


def test_text_can_supply_exact_opening_bpm_key_and_beats_without_audio():
    import json
    import pytest
    from jammate.contracts.adapters import from_text
    from jammate.orchestration.jam_opening import prepare_opening, text_settings
    from jammate.orchestration.llm import ModelReply
    from jammate.orchestration.llm_config import LLMConfig

    class OpeningModel:
        def complete(self, messages, schema, timeout):
            content = json.loads(messages[1]["content"])
            assert content["humming"] == [] and content["rhythm"] is None
            assert content["locked"] == {"tempo_bpm": 92, "key": {"tonic": 2, "mode": "major"},
                                         "rhythm_steps": [0, 6, 12]}
            assert "lead" not in content["instruments"]
            return ModelReply(json.dumps({
                "tempo_bpm": 100, "rhythm_steps": [0, 4, 8, 12], "tonic": 0, "mode": "major",
                "chords": [{"offset": 0, "quality": "maj"}, {"offset": 7, "quality": "maj"}],
                "instruments": ["drums", "harp"], "reason": "Selected ensemble",
            }), "fixture-model", {})

    event = from_text("92 BPM，D 大调，4/4，每小节在第 1、2.5、4 拍起音", session_id="text-opening")
    state, selection = prepare_opening((event,), LLMConfig("test"), {}, provider=OpeningModel())
    assert state.tempo_bpm == 92 and state.key.name == "D major"
    assert selection["settings"]["rhythm_source"] == "text"
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(92, RuleAgent, workers, opening=(state, (event,), selection))
        jam.reserve()
        debug = jam.debug()
        assert [event["source"] for event in debug["opening"]["input_events"]] == ["text"]
        assert {note["instrument"] for note in debug["queued"]["notes"]} == {"drums", "harp"}
        assert {note["start"] for note in debug["queued"]["notes"]} == {0, 1.5, 3}
    with pytest.raises(ValueError, match="4/4"):
        text_settings(from_text("92 BPM，3/4，A 小调", session_id="text-opening"))


def test_slow_model_does_not_block_buffering_and_misses_the_first_open_bar():
    permit = Event()

    class SlowRule(RuleAgent):
        def decide(self, *args, **kwargs):
            assert permit.wait(3)
            return super().decide(*args, **kwargs)

    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, SlowRule, workers)
        jam.reserve()
        zero = jam.snapshot()["next"]
        jam.poll(0, 0, 0)
        jam.submit("鼓少一点")
        try:
            waiting = jam.poll(0, zero["end"] - 1000, 0)
            next_bar = waiting["next"]
            assert waiting["feedback"]["status"] == "thinking"
            assert next_bar["bar"] == 1 and next_bar["start"] == zero["end"]
            assert jam.poll(1, next_bar["start"], 1)["last_applied"] is None
        finally:
            permit.set()
        jam.future.result(timeout=2)
        assert jam.snapshot()["feedback"]["bar"] == 2


def test_transcribed_commands_keep_their_speech_source():
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers)
        jam.reserve()
        jam.poll(0, 0, 0)
        jam.submit("鼓少一点。", source="speech")
        jam.future.result(timeout=2)
        (event,) = jam.manager.current_batch()
        assert event.source == event.event_type == "speech"
        assert event.payload["text"] == "鼓少一点。"
        assert jam.snapshot()["feedback"]["status"] == "scheduled"


def test_continuous_listening_only_routes_directed_or_explicit_music_commands():
    state = JamState(session_id="voice_test", tempo_bpm=96)
    assert voice_command("鼓少一点。", state) == "鼓少一点。"
    assert voice_command("这个不错，鼓再猛一点。", state) == "这个不错，鼓再猛一点。"
    assert voice_command("把速度调到 92 BPM。", state) == "把速度调到 92 BPM。"
    assert voice_command("把速度调到九十二 BPM。", state) == "把速度调到九十二 BPM。"
    assert voice_command("乐队，慢一点。", state) == "慢一点。"
    assert voice_command("JamMate, can you leave some room for me?", state) == "can you leave some room for me?"
    assert voice_command("乐队。", state) is None
    assert voice_command("慢一点。", state) is None
    assert voice_command("慢一点。", state, accept_short=True) == "慢一点。"
    assert voice_command("乐队鼓少一点。", state) == "鼓少一点。"
    assert voice_command("请把鼓少一点吧。", state) == "鼓少一点。"
    assert voice_command("换成 D 大调。", state) == "换成 D 大调。"
    assert voice_command("加一支中音萨克斯。", state) == "加一支中音萨克斯。"
    assert voice_command("我说的是鼓少一点。", state) is None
    assert voice_command("我刚喝了咖啡。鼓少一点。", state) is None
    assert voice_command("乐队今天很忙。", state) is None



def test_spoken_chinese_bpm_numbers_use_the_same_tempo_parser():
    assert tempo_clause("把速度调到九十二 bpm").value == 92
    assert tempo_clause("把速度调到一百零五 bpm").value == 105
    assert tempo_clause("把速度调到一百二十 bpm").value == 120
    assert tempo_clause("再减四 bpm").value == 4
    assert tempo_clause("速度调到两百 bpm").refusal(JamState(session_id="voice_test"))
    assert tempo_clause("速度调到一百二 bpm") is None


def test_live_claps_use_session_tempo_and_require_a_complete_bar():
    import numpy as np
    from jammate.analysis.jam_capture import analyze_capture

    rate = 16000
    capture = np.zeros(3 * rate)
    rng = np.random.default_rng(7)
    for beat in (0, 1, 1.5, 3):
        i = int((.2 + beat * 60 / 96) * rate)
        capture[i:i + 320] = rng.normal(0, .2, 320)
    event, speech = analyze_capture(capture, rate, mode="auto", session_id="claps", bpm=96)
    assert not speech
    assert event.source == "rhythm"
    assert event.payload["tempo_source"] == "session"
    assert event.payload["rhythm"]["onset_beats"] == [0, 1, 1.5, 3]
    assert analyze_capture(capture[:rate * 2], rate, mode="rhythm", session_id="claps", bpm=96) == (None, False)
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers)
        jam.reserve()
        jam.poll(0, 0, 0)
        own_event = analyze_capture(capture, rate, mode="auto", session_id=jam.id, bpm=96)[0]
        assert jam.submit_event(own_event)["source"] == "rhythm"
        jam.future.result(timeout=2)
        assert jam.feedback["status"] == "scheduled"



def test_soundfont_changes_only_unreserved_bars_and_keeps_session_state(monkeypatch):
    import numpy as np
    import pytest

    from jammate.render.soundfont import SoundFontRenderer

    synth = object.__new__(SoundFontRenderer)
    synth.programs = {4: "Electric piano", 19: "Organ", 33: "Bass", 38: "Synth bass", 48: "Strings"}
    renders = []

    def render_all(notes, bpm, sample_rate, *, total_s, timbres):
        renders.append((bpm, dict(timbres)))
        return np.zeros(round(total_s * sample_rate), dtype=np.float32)

    monkeypatch.setattr(synth, "render_all", render_all)
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers, renderer="fluidsynth", soundfont=synth)
        initial = jam.timbres
        motif = jam.manager.state.current_motif_id
        version = jam.manager.state.state_version
        jam.reserve()
        first_packet = jam.packet
        strings = {"keys": 48, "bass": 33}
        organ = {"keys": 19, "bass": 38}
        with pytest.raises(ValueError, match="SoundFont"):
            jam.set_timbres({"keys": True, "bass": 33})
        assert jam.set_timbres(strings)["pending"] == {"bar": 1, "programs": strings}
        assert jam.packet == first_packet

        jam.poll(0, 0, 0)
        second = jam.poll(0, first_packet["end"] - 500, 0)
        assert second["timbres"]["active"] == initial
        assert jam.set_timbres(organ)["pending"] == {"bar": 2, "programs": organ}

        started = jam.poll(1, second["next"]["start"], 1)
        assert started["timbres"] == {"active": strings, "pending": {"bar": 2, "programs": organ}, "applied_at": 1}
        third = jam.poll(1, second["next"]["end"] - 500, 1)
        applied = jam.poll(2, third["next"]["start"], 2)
        assert applied["timbres"] == {"active": organ, "pending": None, "applied_at": 2}
        assert applied["session"] == jam.id and jam.manager.state.current_motif_id == motif
        assert jam.manager.state.state_version == version
        assert renders == [(96, initial), (96, strings), (96, organ)]



def test_automatic_capture_keeps_fragmented_short_speech_for_transcription():
    import numpy as np
    from jammate.analysis.jam_capture import analyze_capture

    rate = 16000
    audio = np.zeros(rate)
    burst = .04 * np.sin(2 * np.pi * 190 * np.arange(800) / rate)
    for start in (800, 2600, 4600):
        audio[start:start + len(burst)] = burst
    assert analyze_capture(audio, rate, mode="auto", session_id="short", bpm=96) == (None, True)
    audio[1600:] = 0
    assert analyze_capture(audio, rate, mode="auto", session_id="short", bpm=96) == (None, False)
