import base64
from contextlib import closing
import io

import numpy as np
import pytest
import soundfile as sf

from jammate.browser_jam import BrowserJamService
from jammate.contracts.adapters import from_rhythm, from_system, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.jam_opening import prepare_opening
from jammate.orchestration.llm import validate_plan
from jammate.orchestration.mrt2_intent import style_plan
from jammate.orchestration.offline import prepare_bar
from jammate.orchestration.transitions import project
from jammate.rhythm import RhythmContext
from jammate.state import JamState


@pytest.mark.parametrize("mode", ["cold", "warm"])
@pytest.mark.parametrize("renderer", ["synth", "mrt2_direct"])
@pytest.mark.parametrize("text", ["给我个 4/4 拍的鼓点", "只要鼓，不要任何和声"])
def test_drum_opening_stays_unpitched_through_32_bars(text, mode, renderer):
    event = from_text(text, session_id="audit")
    state, selection = prepare_opening((event,), None, {}, mode=mode, renderer=renderer)
    assert [part["name"] for part in selection["instruments"]] == ["drums"]
    assert state.key is None and state.chord_cycle == ()
    assert not state.interaction.allow_initiative
    for bar in range(32):
        notes = prepare_bar(state, bar)
        assert {note.instrument for note in notes} == {"drums"}
    for bars in (8, 16, 32):
        idle = from_system("idle", session_id=state.session_id, bars=bars)
        assert RuleAgent().decide((idle,), state).actions == []


@pytest.mark.parametrize("mode", ["cold", "warm"])
def test_scoped_pitched_and_full_openings_preserve_requested_ensemble(mode):
    electric = from_text("只给我一段电吉他 Riff", session_id="audit")
    state, selection = prepare_opening((electric,), None, {}, mode=mode, renderer="mrt2_direct")
    assert selection["style_hint"] == "solo electric guitar riff"
    assert state.key is None and state.chord_cycle == ()
    with pytest.raises(ValueError, match="没有配置电吉他"):
        prepare_opening((electric,), None, {}, mode=mode)
    _, configured = prepare_opening((electric,), None, {27: "Clean Electric Guitar"}, mode=mode, renderer="fluidsynth")
    assert configured["solo_voice"] == "gm_27"

    piano = from_text("给我一段钢琴旋律", session_id="audit")
    _, solo = prepare_opening((piano,), None, {}, mode=mode)
    assert solo["solo_voice"] == "keys"
    assert [part["name"] for part in solo["instruments"]] == ["lead"]
    country = from_text("给我一段完整乡村伴奏", session_id="audit")
    state, _ = prepare_opening((country,), None, {}, mode=mode)
    assert {note.instrument for note in prepare_bar(state, 0)} == {"drums", "keys", "bass"}


@pytest.mark.parametrize("mode", ["cold", "warm"])
def test_claps_alone_do_not_invent_harmony(mode):
    context = RhythmContext(96, (0., 2.), (1., .8))
    event = from_rhythm(context, session_id="audit", audio_duration_s=3, tempo_source="detected")
    state, _ = prepare_opening((event,), None, {}, mode=mode)
    assert state.rhythm == context and state.key is None
    assert {note.instrument for note in prepare_bar(state, 0)} == {"drums"}


@pytest.mark.parametrize("text, expected", [
    ("来点失真电吉他", "distorted electric guitar riffs"),
    ("加入一点迷幻合成器音色", "psychedelic synthesizer"),
    ("我想要萨克斯独奏", "solo saxophone"),
    ("来一些梦幻的氛围音色", "dreamy ambient textures"),
    ("用尼龙弦吉他演奏", "nylon guitar"),
    ("加入带混响的 Shoegaze 吉他", "reverberant shoegaze guitar"),
    ("只突出吉他，其他声部收一点", "prominent guitar, subdued backing parts"),
    ("去掉当前的键盘音色", "no keyboards"),
])
def test_mrt2_timbre_requests_keep_a_renderer_independent_style_intent(text, expected):
    state = JamState(session_id="audit", style="warm jazz, piano")
    event = from_text(text, session_id=state.session_id)
    plan = style_plan((event,), state)
    assert plan is not None and plan.actions == []
    assert expected in plan.to_dict()["style"]
    validate_plan(plan, (event,), state)
    updated = project(state, plan)
    assert updated.layers == state.layers and updated.key == state.key
    assert updated.style == plan.to_dict()["style"]
    assert JamState.from_dict(updated.to_dict()) == updated


def test_user_can_pause_and_restore_explicit_idle_initiative():
    agent = RuleAgent()
    initial = JamState(session_id="audit")
    keep = from_text("保持这个节奏，不要加其他乐器", session_id="audit")
    state = project(initial, agent.decide((keep,), initial))
    assert not state.interaction.allow_initiative
    idle = from_system("idle", session_id="audit", bars=8)
    assert agent.decide((idle,), state).actions == []
    improvise = from_text("你可以自由发挥", session_id="audit")
    state = project(state, agent.decide((improvise,), state))
    assert state.interaction.allow_initiative
    assert agent.decide((idle,), state).to_dict()["strategy"] == "INITIATE"


def test_rhythm_sketch_uses_clip_duration_without_relaxing_strict_capture():
    rate = 16000
    audio = np.zeros(rate)
    transient = np.random.default_rng(42).normal(0, .18, 480)
    for offset in (1600, 6720, 11200):
        audio[offset:offset + len(transient)] = transient
    stream = io.BytesIO()
    sf.write(stream, audio, rate, format="WAV")
    clip = {"mime_type": "audio/wav", "audio": base64.b64encode(stream.getvalue()).decode()}
    with closing(BrowserJamService()) as service:
        with pytest.raises(ValueError, match="未能从拍手定速"):
            service.opening_events({"rhythm": clip})
        event, = service.opening_events({"rhythm": clip, "rhythm_timing": "feel"}, bpm=96)
    assert event.payload["audio_duration_s"] == 1
    assert event.payload["tempo_source"] == "session"
    assert len(event.payload["rhythm"]["onset_beats"]) >= 2


def test_opt_in_accepts_audible_audio_without_inventing_pitch(monkeypatch):
    monkeypatch.setattr("jammate.analysis.jam_capture.segment_notes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("jammate.analysis.jam_capture.track_pitch", lambda *_args, **_kwargs: ())
    rate = 16000
    audio = .2 * np.sin(2 * np.pi * 220 * np.arange(rate) / rate)
    stream = io.BytesIO()
    sf.write(stream, audio, rate, format="WAV")
    clip = {"mime_type": "audio/wav", "audio": base64.b64encode(stream.getvalue()).decode()}
    references = {}
    with closing(BrowserJamService()) as service:
        with pytest.raises(ValueError, match="没有识别到音符"):
            service.opening_events({"humming": clip})
        event, = service.opening_events({"humming": clip}, audio_references=references)
    assert event.payload["melody"]["status"] == "no_notes"
    assert event.payload["melody"]["notes"] == []
    assert event.to_dict()["provenance"]["audio_ref"] == f"jam:{event.session_id}/{event.event_id}"
    assert references[event.event_id][1] == rate
