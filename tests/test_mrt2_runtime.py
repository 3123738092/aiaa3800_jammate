"""Unit tests: scripted providers/model output, NEVER real model inference."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import mido
import numpy as np
import pretty_midi
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer_experiments.app import band_plan
from renderer_experiments.app.mrt2 import MRT2Runtime
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text, from_system
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.render.capabilities import BackendCapabilityError
from jammate.render.mrt2 import generate, midi_conditioning
from jammate.state import JamState


def staged(agent=None):
    m = StateManager(JamState(session_id="paired", tempo_bpm=100))
    events = m.ingest([from_text("鼓更猛一点", session_id="paired", event_id="e1")])
    plan = (agent or RuleAgent()).decide(events, m.state, plan_id="paired_plan")
    assert m.stage(plan)
    return m, plan


def scripted_audio(prompt, *, output, duration, **kwargs):
    x = .05*np.sin(np.arange(round(duration*48000))*2*np.pi*220/48000)
    sf.write(output, np.column_stack((x, x)), 48000, subtype="PCM_16")
    return {"inference": "injected_test_double"}


def test_strict_mrt2_rejects_before_model_or_state_change(tmp_path):
    m, plan = staged()
    before = m.state
    r = MRT2Runtime(tmp_path, generator=lambda *a, **k: pytest.fail("model must not run"))
    with pytest.raises(BackendCapabilityError) as caught:
        r.execute(m, plan)
    assert {x["constraint"] for x in caught.value.report["preserve"]} >= {"tempo", "key", "layer:bass"}
    assert not any(x["guaranteed"] for x in caught.value.report["preserve"])
    assert m.state == before and m.current_batch()
    assert r.last_receipt["artifact_generated"] is False


def test_preview_has_audio_but_never_commits_fictional_notes_or_effects(tmp_path):
    m, plan = staged()
    before = m.state
    r = MRT2Runtime(tmp_path, allow_preview=True, generator=scripted_audio)
    rendered = r.execute(m, plan)
    assert rendered.audio.shape[1] == 2
    assert m.state == before and m.current_batch()
    assert r.last_receipt["phase"] == "preview"
    assert not r.last_receipt["state_committed"] and not r.last_receipt["succeeded"]
    assert r.last_receipt["artifact_generated"]
    assert r.last_receipt["midi_role"] == "conditioning_not_transcription"
    with pytest.raises(PlanError):
        r.execute(m, plan)


def test_mrt2_stop_is_host_silence_and_can_commit_without_model(tmp_path):
    m = StateManager(JamState(session_id="stop"))
    events = m.ingest([from_system("stop", session_id="stop")])
    plan = RuleAgent().decide(events, m.state, plan_id="stop")
    m.stage(plan)
    r = MRT2Runtime(tmp_path, generator=lambda *a, **k: pytest.fail("stop must not need model"))
    result = r.execute(m, plan)
    assert not np.any(result.audio) and not m.state.playing
    assert r.last_receipt["state_committed"]
    assert r.last_receipt["inference"] == "host_silence_no_model"


class Provider:
    def complete(self, messages, schema, timeout_s):
        return ModelReply(json.dumps({"strategy": "FOLLOW", "valid_for_bars": 4,
            "preserve": ["tempo", "key", "layer:bass", "layer:keys", "layer:lead"],
            "actions": [{"action": "BUILD", "scope": "drums", "strength": .5,
                         "parameters": {"duration_bars": 4}}],
            "transport": {"playing": None, "tempo_bpm": None}, "reason": "scripted test", "unresolved": []}),
            "scripted-provider", {})


@pytest.mark.parametrize("agent_mode", ["rule", "llm"])
def test_same_input_and_plan_share_midi_timing_and_receipts_across_backends(tmp_path, monkeypatch, agent_mode):
    agent = RuleAgent() if agent_mode == "rule" else LLMAgent(LLMConfig(api_key="test-fixture"), provider=Provider())
    m1, plan = staged(agent)
    m2, _ = staged(agent)
    assert m1.state == m2.state
    sf2 = tmp_path / "fixture.sf2"
    sf2.write_bytes(b"not a soundfont; synthesis is mocked")
    def fake_fs(midi, wav, executable, font, sr):
        t = np.arange(round(mido.MidiFile(midi).length*sr))/sr
        sf.write(wav, .05*np.sin(2*np.pi*220*t), sr)
    monkeypatch.setattr(band_plan, "render_with_fluidsynth", fake_fs)
    exact = band_plan.KevinRuntime(tmp_path/"fs", executable=tmp_path/"fs.exe", soundfont=sf2)
    generated = MRT2Runtime(tmp_path/"mrt", allow_preview=True, generator=scripted_audio)
    exact.execute(m1, plan)
    generated.execute(m2, plan)
    assert (exact.last_artifacts/"band.mid").read_bytes() == (generated.last_artifacts/"band.mid").read_bytes()
    for key in ("session_id", "plan_id", "based_on_state_version", "effective_bar", "completed_bar", "midi_verification"):
        assert exact.last_receipt[key] == generated.last_receipt[key]
    assert exact.last_receipt["state_committed"] and not generated.last_receipt["state_committed"]
    assert m1.state.layer("drums").level > m2.state.layer("drums").level


def melody(path):
    midi = pretty_midi.PrettyMIDI(initial_tempo=120)
    part = pretty_midi.Instrument(73)
    part.notes = [pretty_midi.Note(90, 60, .081, .085), pretty_midi.Note(90, 60, .12, .2)]
    drums = pretty_midi.Instrument(0, is_drum=True)
    drums.notes = [pretty_midi.Note(90, 36, .04, .1)]
    midi.instruments = [part, drums]
    midi.write(str(path))


def test_real_smf_tokens_short_notes_retriggers_and_drums(tmp_path):
    path = tmp_path/"input.mid"
    melody(path)
    notes, drums = midi_conditioning(path, .4)
    assert notes.shape == (10, 128) and drums.shape == (10, 1)
    assert notes[2, 60] == 2 and notes[3, 60] == 2 and notes[4, 60] == 1
    assert not notes[:, 36].any() and drums[1, 0] == 1
    with pytest.raises(ValueError, match="truncation"):
        midi_conditioning(path, .1)


def test_midi_tempo_map_is_used_instead_of_assuming_120_bpm(tmp_path):
    path = tmp_path/"tempo.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("set_tempo", tempo=1000000, time=480),
        mido.Message("note_on", note=64, velocity=90, time=480),
        mido.Message("note_off", note=64, time=480)])
    midi.tracks.append(track)
    midi.save(path)
    notes, _ = midi_conditioning(path, 3.)
    assert notes[37, 64] == 2 and notes[62, 64] == 1 and notes[63, 64] == 0


class Model:
    def __init__(self):
        self.calls = []
    def embed_style(self, prompt, use_mapper):
        assert use_mapper
        return prompt
    def tokenize_style(self, embedding):
        return np.arange(12)
    def generate(self, *, conditioning, frames, state):
        assert "mulan_tokens_25hz" in conditioning
        assert state == (len(self.calls) if self.calls else None)
        self.calls.append((conditioning, frames))
        return SimpleNamespace(sample_rate=48000, samples=np.full((frames*1920, 2), .01)), len(self.calls)


@pytest.mark.parametrize("with_midi", [False, True])
def test_official_model_api_frame_conditions_and_continuation(tmp_path, with_midi):
    path = tmp_path/"melody.mid"
    melody(path)
    model = Model()
    report = generate("warm trio", output=tmp_path/"output.wav", duration=.41,
                      midi=path if with_midi else None, system=model)
    assert report["inference"] == "injected_test_double"
    assert sum(frames for _, frames in model.calls) == 11
    if with_midi:
        assert len(model.calls) > 1
        assert all(len(cond["pianoroll_with_onsets_tokens"]) == 128 for cond, _ in model.calls)
    else:
        assert len(model.calls) == 1
        assert "pianoroll_with_onsets_tokens" not in model.calls[0][0]
    audio, sr = sf.read(tmp_path/"output.wav")
    assert audio.shape == (round(.41*48000), 2) and sr == 48000


def test_cancelled_generation_never_commits_preview_or_success(tmp_path):
    m, plan = staged()
    def cancellation(*args, **kwargs):
        scripted_audio(*args, **kwargs)
        m.ingest([from_text("停止", session_id="paired", event_id="new")])
        return {"inference": "injected_test_double"}
    r = MRT2Runtime(tmp_path, allow_preview=True, generator=cancellation)
    with pytest.raises(PlanError):
        r.execute(m, plan)
    assert m.plan_status(plan.plan_id) == "cancelled"
    assert not r.last_receipt["state_committed"]


def test_missing_official_dependency_is_a_recoverable_error(tmp_path, monkeypatch):
    import jammate.render.mrt2 as module
    def missing(*args):
        raise ImportError("fixture missing dependency")
    monkeypatch.setattr(module, "load_system", missing)
    with pytest.raises(RuntimeError, match="server's Python"):
        generate("warm trio", output=tmp_path/"missing.wav", duration=.1)
    assert not (tmp_path/"missing.wav").exists()
