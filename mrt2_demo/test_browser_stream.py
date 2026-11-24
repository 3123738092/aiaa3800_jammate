from concurrent.futures import ThreadPoolExecutor
from threading import get_ident
from types import SimpleNamespace

import numpy as np
import pytest

from jammate.browser_jam import BrowserJam
from jammate.music import Note
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.realtime import PlaybackBar
from jammate.render.clock import BarClock
from jammate.render.synth import SR

from . import browser_stream as stream
from .inference import DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE


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
    playback = stream.BrowserMRT2Stream("warm band", "mrt2_small")
    bar0 = PlaybackBar(clock.reserve(96), (Note(60, 0, .5),))
    bar1 = PlaybackBar(clock.reserve(96), (Note(38, 4, .5, instrument="drums"),))
    pcm0 = consume(playback.open_bar(bar0, SR))
    with ThreadPoolExecutor(1) as next_request:
        pcm1 = next_request.submit(consume, playback.open_bar(bar1, SR)).result(timeout=2)

    assert pcm0.shape == pcm1.shape == (120000, 2)
    assert len(model.frames) == 125
    assert model.frames[0][0][PITCH_KEY][60] == 2
    assert model.frames[63][0][DRUM_KEY] == [1] and model.frames[63][1] == 63
    assert np.array_equal(pcm1[:960], np.tile(pcm0[-1], (960, 1)))
    assert model.prompts == ["warm band"] and len(model.threads) == 1


def test_silence_does_not_load_model_and_disconnect_ends_stream(model):
    clock = BarClock(SR)
    playback = stream.BrowserMRT2Stream("warm band", "mrt2_small")
    silent = PlaybackBar(clock.reserve(96), ())
    current = PlaybackBar(clock.reserve(96), (Note(60, 4, .5),))
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
        jam = BrowserJam(96, RuleAgent, workers, renderer="mrt2_stream")
        jam.reserve()
        first = jam.snapshot()["next"]
        assert first["pcm_frames"] == 120000 and "wav" not in first
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
        assert model.frames[-1][1] == 124 and len(model.prompts) == 1
        jam.close()
