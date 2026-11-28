from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

import numpy as np
import pytest

from jammate.browser_jam import BrowserJam, BrowserJamService
from jammate.interaction import Layer
from jammate.music import Note
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.realtime import PlaybackBar
from jammate.render.clock import BarClock
from jammate.render.synth import SR
from jammate.state import JamState

from . import browser_stream as stream
from .inference import DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE, STYLE_KEY


@pytest.fixture
def model(monkeypatch):
    class Engine:
        def __init__(self):
            self.frames = []
            self.prompts = []
            self.threads = set()

        def embed_style(self, prompt, *, use_mapper):
            assert use_mapper
            self.prompts.append(prompt)
            self.threads.add(get_ident())
            return prompt

        def tokenize_style(self, embedding):
            return list(range(12))

        def generate(self, *, conditioning, frames, state):
            assert frames == 1
            self.frames.append((conditioning, state))
            self.threads.add(get_ident())
            step = (state or 0) + 1
            return SimpleNamespace(sample_rate=SAMPLE_RATE,
                                   samples=np.full((FRAME_SAMPLES, 2), step / 1024, dtype=np.float32)), step

    engine = Engine()
    monkeypatch.setattr(stream, "_load_stream_model", lambda name: engine)
    monkeypatch.setattr(stream, "STREAM_LEAD_S", 10.)
    return engine


def consume(context):
    with context as (length, chunks):
        pcm = b"".join(chunks)
    assert len(pcm) == length
    return np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)


def test_preserves_model_state_and_fractional_frame_between_bars(model):
    clock = BarClock(SR)
    state = JamState(tempo_bpm=96)
    playback = stream.BrowserMRT2Stream("warm band", "mrt2_small")
    bar0 = PlaybackBar(clock.reserve(96), (Note(60, 0, .5),), state=state)
    bar1 = PlaybackBar(clock.reserve(96), (Note(38, 4, .5, instrument="drums"),), state=state)
    pcm0 = consume(playback.open_bar(bar0, SR))
    with ThreadPoolExecutor(1) as next_request:
        pcm1 = next_request.submit(consume, playback.open_bar(bar1, SR)).result(timeout=2)

    assert pcm0.shape == pcm1.shape == (120000, 2)
    assert len(model.frames) == 125
    assert model.frames[0][0][PITCH_KEY][60] == 2
    assert model.frames[63][0][DRUM_KEY] == [1] and model.frames[63][1] == 63
    assert np.array_equal(pcm1[:960], np.tile(pcm0[-1], (960, 1)))
    assert len(model.prompts) == 1 and "warm band" in model.prompts[0] and len(model.threads) == 1


@pytest.mark.parametrize("removal_tempo", [96, 100])
def test_removing_electric_guitar_resumes_only_compatible_backing(model, removal_tempo):
    clock = BarClock(SR)
    backing = JamState(tempo_bpm=96, layers=(Layer("drums"), Layer("bass"),
        Layer("keys", active=False), Layer("lead", active=False)))
    full = replace(backing, layers=(*backing.layers, Layer("gm_27")))
    playback = stream.BrowserMRT2Stream("solo drum groove", "mrt2_small")

    for index, state in enumerate((backing, full, replace(backing, tempo_bpm=removal_tempo))):
        notes = (Note(38, 4 * index, 1., instrument="bass"),)
        if state is full:
            notes += (Note(69, 4 * index, .5, instrument="gm_27"),
                      Note(72, 4 * index + .5, .5, instrument="gm_27"))
        rendered = consume(playback.open_bar(PlaybackBar(clock.reserve(state.tempo_bpm), notes, state=state), SR))
        if index == 1:
            last_full = rendered[-1]

    matching = removal_tempo == 96
    assert "clean electric lead guitar playing a clear melodic riff" in model.prompts[1]
    assert "guitar" not in model.prompts[-1]
    assert model.frames[125][1] == (63 if matching else 125)
    assert playback.diagnostics()["recent_bars"][-1]["backing_context_restored"] is matching
    if matching:
        np.testing.assert_array_equal(rendered[0], last_full)
        assert rendered[300, 0] == 64 * 32


def test_guitar_chords_keep_the_original_style_condition(model):
    clock = BarClock(SR)
    state = JamState(tempo_bpm=96, layers=(Layer("drums"), Layer("bass"), Layer("gm_27"),
        Layer("keys", active=False), Layer("lead", active=False)))
    playback = stream.BrowserMRT2Stream("solo drum groove", "mrt2_small")
    chords = tuple(Note(pitch, 0., .5, instrument="gm_27") for pitch in (60, 64, 67))
    consume(playback.open_bar(PlaybackBar(clock.reserve(96), chords, state=state), SR))
    assert model.prompts == ["drum kit, bass, electric guitar (clean), drum groove"]


@pytest.mark.parametrize(("tempo", "beats"), [(120, 4), (110, 2)])
def test_mrt2_guitar_mix_protects_backing_and_replays_the_same_pcm(monkeypatch, model, tempo, beats):
    monkeypatch.setattr(stream, "SCORE_STREAM_LEAD_S", 10.)
    # The fake model emits the same premixed guitar and bass in all three render paths.
    def generate(*, conditioning, frames, state):
        step = state or 0
        t = np.arange(step * FRAME_SAMPLES, (step + 1) * FRAME_SAMPLES) / SAMPLE_RATE
        wave = .12 * np.sin(2 * np.pi * 440 * t) + .20 * np.sin(2 * np.pi * 98 * t)
        return SimpleNamespace(sample_rate=SAMPLE_RATE,
                               samples=np.column_stack((wave, wave)).astype(np.float32)), step + 1

    monkeypatch.setattr(model, "generate", generate)
    backing = (Layer("bass"), Layer("drums"), Layer("keys", active=False), Layer("lead", active=False))
    state = JamState(tempo_bpm=tempo, beats_per_bar=beats, layers=(*backing, Layer("gm_27", level=.7)))
    notes = (Note(43, 0., beats, instrument="bass"), Note(69, 0., beats, instrument="gm_27"))
    timing = BarClock(SR, beats_per_bar=beats).reserve(tempo)
    focused = PlaybackBar(timing, notes, state=state)
    plain = replace(focused, state=replace(state, layers=(*backing, Layer("gm_27"))))
    pcm = stream.BrowserMRT2Stream("drum groove", "mrt2_small")
    encoded = stream.BrowserMRT2Stream("drum groove", "mrt2_small")
    dry = consume(stream.BrowserMRT2Stream("drum groove", "mrt2_small").open_bar(plain, SR))
    wet = consume(pcm.open_bar(focused, SR))
    archive = consume(encoded.open_bar(focused, SR, replayable=True))
    replay = consume(encoded.open_bar(focused, SR, replayable=True))

    assert wet.shape == dry.shape == (pcm.samples_for_bar(timing, SR), 2)
    np.testing.assert_array_equal(wet, archive)
    np.testing.assert_array_equal(wet, replay)
    np.testing.assert_array_equal(wet[[0, -1]], dry[[0, -1]])
    window = slice(SAMPLE_RATE // 4, 3 * SAMPLE_RATE // 4)
    t = np.arange(window.start, window.stop) / SAMPLE_RATE

    def amplitude(x, hz):
        return abs(np.mean(x[window, 0] * np.exp(-2j * np.pi * hz * t)))

    assert amplitude(wet, 440) > 1.2 * amplitude(dry, 440)
    assert amplitude(wet, 98) == pytest.approx(amplitude(dry, 98), rel=.005)
    assert pcm.diagnostics()["recent_bars"][-1]["guitar_focus_db"] == pytest.approx(6.)
    assert encoded.diagnostics()["replayed_bars"] == 1


def test_encoded_requests_overlap_egress_and_can_replay_earlier_bars(model):
    clock = BarClock(SR)
    state = JamState(tempo_bpm=96)
    playback = stream.BrowserMRT2Stream("warm band", "mrt2_small")
    bars = [PlaybackBar(clock.reserve(96), (Note(60 + i, i * 4, .5),), state=state) for i in range(3)]
    with ThreadPoolExecutor(2) as requests:
        early = requests.submit(consume, playback.open_bar(bars[1], SR, replayable=True))
        # An out-of-order request waits for its predecessor to generate.
        with pytest.raises(FutureTimeout):
            early.result(timeout=.1)
        with playback.open_bar(bars[0], SR, replayable=True) as (size0, first_response):
            # Keep the first response open while the next bars are generated.
            pcm1 = early.result(timeout=2)
            pcm2 = requests.submit(consume, playback.open_bar(bars[2], SR, replayable=True)).result(timeout=2)
            pcm0 = b"".join(first_response)
            assert len(pcm0) == size0
    duplicate = consume(playback.open_bar(bars[0], SR, replayable=True))
    assert duplicate.tobytes() == pcm0
    assert len(model.frames) == 188 and playback.diagnostics()["replayed_bars"] == 1
    assert model.frames[0][0][PITCH_KEY][60] == 2
    assert model.frames[63][0][PITCH_KEY][61] == 2
    assert model.frames[125][0][PITCH_KEY][62] == 2
    assert np.array_equal(pcm1[:960], np.tile(duplicate[-1], (960, 1)))
    assert pcm2[0, 0] > pcm1[0, 0]


def test_silence_does_not_load_model_and_disconnect_ends_stream(model):
    clock = BarClock(SR)
    state = JamState(tempo_bpm=96)
    playback = stream.BrowserMRT2Stream("warm band", "mrt2_small")
    silent = PlaybackBar(clock.reserve(96), (), state=state)
    current = PlaybackBar(clock.reserve(96), (Note(60, 4, .5),), state=state)
    assert not np.any(consume(playback.open_bar(silent, SR)))
    assert not model.prompts and not model.frames

    with pytest.raises(BrokenPipeError):
        with playback.open_bar(current, SR) as (_, chunks):
            assert next(chunks)
            raise BrokenPipeError()
    with pytest.raises(ValueError, match="已失效"):
        consume(playback.open_bar(current, SR))


def test_browser_stream_updates_conditions_at_the_scheduled_bar(model):
    with ThreadPoolExecutor(1) as workers:
        # Leave room to submit a change before the next bar is reserved.
        jam = BrowserJam(80, RuleAgent, workers, renderer="mrt2_stream")
        jam.reserve()
        first = jam.snapshot()["next"]
        assert first["pcm_frames"] == 144000 and "wav" not in first
        consume(jam.open_mrt2_bar(0))
        jam.poll(0, 0, 0)
        jam.submit("鼓少一点")
        jam.future.result(timeout=2)
        pending = jam.poll(0, first["end"] - 1000, 0)
        assert pending["feedback"]["status"] == "scheduled"
        assert pending["next"]["bar"] == 1 and "wav" not in pending["next"]
        assert jam.manager.state.layer("drums").density == 1
        consume(jam.open_mrt2_bar(1))
        playing = jam.poll(1, first["end"], 1)
        assert playing["feedback"]["status"] == "applied"
        assert jam.manager.state.layer("drums").density < 1
        assert model.frames[-1][1] == 149
        jam.close()


def test_direct_browser_stream_runs_bandplan_without_score_or_backing(model, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("MRT2 direct attempted to compile a score or render a backing track")

    monkeypatch.setattr(BrowserJamService, "mrt2_stream_available", staticmethod(lambda: True))
    monkeypatch.setattr("jammate.orchestration.realtime.prepare_plan", unexpected)
    monkeypatch.setattr("jammate.orchestration.realtime.prepare_bar", unexpected)
    monkeypatch.setattr("jammate.browser_jam.render", unexpected)
    monkeypatch.setattr("jammate.browser_jam.LLMBackendArranger", unexpected)
    service = BrowserJamService()
    try:
        assert service.dispatch("status", {})["mrt2_direct"]
        view = service.dispatch("start", {"renderer": "mrt2_direct", "opening_mode": "cold",
                                         "arranger": "llm_all", "opening": {"text": "soft jazz", "key": "C 大调"}})
        jam = service.sessions[view["session"]]
        assert jam.arrangement_config is None
        packet = view["next"]
        assert packet["pcm_frames"] == 120000 and "wav" not in packet
        assert jam.reserved.plan.plan_id == jam.opening["mrt2_plan"]["plan_id"]
        assert np.any(consume(service.open_mrt2_bar(jam.id, 0)))
        jam.poll(0, packet["start"], 0)
        assert jam.opening["playback_receipt"]["plan_id"] == jam.opening["mrt2_plan"]["plan_id"]

        for index, command in enumerate(("加入鼓", "停止"), start=1):
            jam.submit(command)
            jam.future.result(timeout=2)
            queued = jam.poll(index - 1, packet["end"] - 1000, index - 1)
            assert queued["feedback"]["status"] == "scheduled"
            packet = queued["next"]
            assert packet["bar"] == index and "wav" not in packet
            assert jam.reserved.plan.plan_id == queued["feedback"]["plan"]
            assert jam.reserved.notes == ()
            pcm = consume(service.open_mrt2_bar(jam.id, index))
            playing = jam.poll(index, packet["start"], index)
            assert playing["feedback"]["status"] == "applied"
            assert bool(np.any(pcm)) is (index == 1)
            assert playing["preview_id"] is None and playing["auto_preview_id"] is None

        assert model.prompts == ["soft jazz, C major", "soft jazz, C major, drum kit"]
        assert all(set(conditioning) == {STYLE_KEY} for conditioning, _ in model.frames)
        assert model.frames[-1][1] == 124
    finally:
        service.close()


def test_direct_motif_plan_does_not_claim_generated_notes(model):
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers, renderer="mrt2_direct")
        try:
            jam.reserve()
            first = jam.snapshot()["next"]
            consume(jam.open_mrt2_bar(0))
            jam.poll(0, 0, 0)
            jam.submit("来一段")
            jam.future.result(timeout=2)
            queued = jam.poll(0, first["end"] - 1000, 0)
            assert queued["feedback"]["status"] == "scheduled"
            assert np.any(consume(jam.open_mrt2_bar(1)))
            playing = jam.poll(1, first["end"], 1)
            assert playing["feedback"]["status"] == "applied"
            assert "Introduce a new lead melody" in model.prompts[-1]
            assert not jam.manager.state.last_ai_motif and not jam.manager.state.motifs
        finally:
            jam.close()
