from concurrent.futures import ThreadPoolExecutor
from threading import Event

from jammate.browser_jam import BrowserJam, voice_command
from jammate.orchestration.agent import RuleAgent
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

        assert buffered["feedback"]["status"] == "scheduled"
        assert buffered["next"]["start"] == zero["end"]
        assert jam.manager.state.layer("drums").density == 1
        assert jam.snapshot(buffered=1)["next"] is None

        applied = jam.poll(1, zero["end"], 1)
        drums, bass, keys = applied["layers"]
        assert applied["feedback"]["status"] == "applied"
        assert applied["last_applied"]["bar"] == 1 and applied["bpm"] == 96
        assert drums["density"] < 1 and bass["density"] == keys["density"] == 1
        version = jam.manager.state.state_version
        jam.poll(1, zero["end"] + 10, 1)
        assert jam.manager.state.state_version == version


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
