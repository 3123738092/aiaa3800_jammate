import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from jammate.contracts.adapters import from_system, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.backend_arrangement import LLMBackendArranger
from jammate.orchestration.llm import ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.render.capabilities import BackendCapabilityError
from jammate.render.runtime import create_runtime
from jammate.state import JamState

from . import jammate_bridge
from .inference import DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE, STYLE_KEY


@pytest.fixture
def engine(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.calls = []

        def embed_style(self, prompt, *, use_mapper):
            assert prompt and use_mapper
            return prompt

        def tokenize_style(self, embedding):
            return [3] * 12

        def generate(self, *, conditioning, frames, state):
            assert conditioning[STYLE_KEY] == [3] * 12 and frames == 1
            self.calls.append((dict(conditioning), state))
            samples = np.full((FRAME_SAMPLES, 2), .01, dtype=np.float32)
            return SimpleNamespace(samples=samples, sample_rate=SAMPLE_RATE), (state or 0) + 1

    model = FakeModel()
    monkeypatch.setattr(jammate_bridge, '_load_mlxfn', lambda size: model)
    monkeypatch.setenv('JAMMATE_MRT2_ENGINE', 'mlx')
    return model


def staged(event):
    manager = StateManager(JamState(session_id=event.session_id, tempo_bpm=180))
    events = manager.ingest([event])
    plan = RuleAgent().decide(events, manager.state, plan_id='test')
    manager.stage(plan)
    return manager, plan


def test_runtime_midi_preview_uses_bridge_and_does_not_commit(tmp_path, engine):
    manager, plan = staged(from_text('鼓更猛一点', session_id='test'))
    before = manager.state
    runtime = create_runtime(tmp_path, backend='mrt2', generator=jammate_bridge.generate,
                             allow_preview=True)
    clip = runtime.execute(manager, plan)
    assert clip.sample_rate == SAMPLE_RATE and clip.audio.shape[1] == 2
    assert runtime.last_receipt['conditioning'] == 'text_and_midi'
    assert runtime.last_receipt['midi_sha256']
    assert runtime.last_receipt['phase'] == 'preview'
    assert runtime.last_receipt['state_committed'] is False and manager.state == before
    assert engine.calls[0][1] is None and engine.calls[1][1] == 1
    assert any(2 in conditioning[PITCH_KEY] for conditioning, _ in engine.calls)
    assert any(conditioning[DRUM_KEY] == [1] for conditioning, _ in engine.calls)


def test_direct_conditions_keep_onset_and_trim_partial_frame(tmp_path, engine):
    notes = np.zeros((2, 128), dtype=np.int32)
    notes[:, 60] = [2, 1]
    drums = np.array([[0], [1]])
    output = tmp_path / 'direct.wav'
    receipt = jammate_bridge.generate('acoustic trio', output=output, duration=.055,
                                      conditions=(notes, drums), backend='mlx')
    assert receipt['conditioning'] == 'text_and_direct_frames' and receipt['midi_sha256'] is None
    assert [cond[PITCH_KEY][60] for cond, _ in engine.calls] == [2, 1]
    assert [cond[DRUM_KEY] for cond, _ in engine.calls] == [[0], [1]]
    assert sf.info(output).frames == round(.055 * SAMPLE_RATE)
    jammate_bridge.generate('piano', output=tmp_path / 'text.wav', duration=.04, backend='mlx')
    cond, state = engine.calls[-1]
    assert set(cond) == {STYLE_KEY} and state is None


def test_runtime_direct_arrangement_supplies_frames_to_bridge(tmp_path, engine):
    class ScriptedProvider:
        def complete(self, messages, schema, timeout_s):
            return ModelReply(json.dumps({'style': 'dry jazz trio',
                                          'pitch_spans': [{'frame': 1, 'length': 2, 'pitch': 60}],
                                          'drum_spans': [{'frame': 0, 'length': 1}]}), 'test', {})

    arranger = LLMBackendArranger(LLMConfig(api_key='fixture'), 'mrt2', provider=ScriptedProvider())
    manager, plan = staged(from_text('鼓更猛一点', session_id='direct'))
    runtime = create_runtime(tmp_path, backend='mrt2', generator=jammate_bridge.generate,
                             backend_arranger=arranger, allow_preview=True)
    runtime.execute(manager, plan)
    assert (runtime.last_artifacts / 'conditioning.json').is_file()
    assert not (runtime.last_artifacts / 'band.mid').exists()
    assert runtime.last_receipt['conditioning'] == 'text_and_direct_frames'
    assert runtime.last_receipt['state_committed'] is False
    assert [cond[PITCH_KEY][60] for cond, _ in engine.calls[:3]] == [0, 2, 1]


def test_invalid_conditions_fail_before_loading_model(tmp_path, engine):
    output = tmp_path / 'bad.wav'
    with pytest.raises(ValueError, match='pitch/drum'):
        jammate_bridge.generate('piano', output=output, duration=.04, backend='mlx',
                                conditions=(np.full((1, 128), 3), np.zeros((1, 1))))
    with pytest.raises(ValueError, match='either direct conditions or MIDI'):
        jammate_bridge.generate('piano', output=output, duration=.04, backend='mlx',
                                midi=tmp_path / 'input.mid', conditions=([], []))
    assert not engine.calls and not output.exists()


def test_strict_preview_and_stop_keep_runtime_semantics(tmp_path, engine):
    runtime = create_runtime(tmp_path, backend='mrt2', generator=jammate_bridge.generate)
    manager, plan = staged(from_text('鼓更猛一点', session_id='strict'))
    with pytest.raises(BackendCapabilityError):
        runtime.execute(manager, plan)
    manager, plan = staged(from_system('stop', session_id='stop'))
    clip = runtime.execute(manager, plan)
    assert not np.any(clip.audio) and runtime.last_receipt['state_committed']
    assert not engine.calls
