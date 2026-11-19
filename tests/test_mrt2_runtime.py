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


class BackendProvider:
    def __init__(self, output):
        self.output = output
        self.context = None

    def complete(self, messages, schema, timeout_s):
        self.context = json.loads(messages[-1]["content"])
        assert self.context["band_plan"]["plan_id"] == "paired_plan"
        assert self.context["input_events"][0]["payload"]["text"] == "鼓更猛一点"
        assert "groups" not in self.context and "performance" not in self.context
        return ModelReply(json.dumps(self.output), "native-arranger-test", {})


def test_fluidsynth_llm_supplies_targeted_midi_events_before_reference_compilation(tmp_path, monkeypatch):
    from jammate.orchestration.backend_arrangement import LLMBackendArranger

    manager, plan = staged()
    provider = BackendProvider({"events": [{"channel": 9, "pitch": 56, "tick": 240, "length_ticks": 60, "velocity": 47}]})
    arranger = LLMBackendArranger(LLMConfig(api_key="fixture-key"), "fluidsynth", provider=provider)
    original = band_plan.prepare_plan

    def guarded_prepare(*args, **kwargs):
        assert provider.context is not None
        return original(*args, **kwargs)

    def fake_fs(midi, wav, executable, font, sr):
        t = np.arange(round(mido.MidiFile(midi).length * sr)) / sr
        sf.write(wav, .05 * np.sin(2 * np.pi * 220 * t), sr)

    monkeypatch.setattr(band_plan, "prepare_plan", guarded_prepare)
    monkeypatch.setattr(band_plan, "render_with_fluidsynth", fake_fs)
    font = tmp_path / "fake.sf2"
    font.write_bytes(b"fixture")
    runtime = band_plan.KevinRuntime(tmp_path, executable=tmp_path / "fs", soundfont=font, backend_arranger=arranger)
    runtime.execute(manager, plan)
    assert [item["channel"] for item in provider.context["writable_channels"]] == [9]
    midi = mido.MidiFile(runtime.last_artifacts / "band.mid")
    drums = [event.note for event in midi if event.type == "note_on" and event.channel == 9]
    assert drums == [56]
    assert runtime.last_receipt["state_committed"] and runtime.last_receipt["arrangement"]["status"] == "accepted"


def test_mrt2_llm_feeds_direct_frame_tokens_and_never_compiles_midi(tmp_path, monkeypatch):
    from jammate.orchestration.backend_arrangement import LLMBackendArranger

    manager, plan = staged()
    before = manager.state
    provider = BackendProvider({"style": "dry and punchy drums", "pitch_spans": [{"frame": 2, "length": 3, "pitch": 60}],
                                "drum_spans": [{"frame": 2, "length": 1}]})
    arranger = LLMBackendArranger(LLMConfig(api_key="fixture-key"), "mrt2", provider=provider)
    monkeypatch.setattr(band_plan, "prepare_plan", lambda *a, **k: pytest.fail("MRT2 must bypass score compilation"))
    monkeypatch.setattr(band_plan, "write_midi", lambda *a, **k: pytest.fail("MRT2 must bypass MIDI export"))

    def generator(style, *, output, duration, conditions, **kwargs):
        notes, drums = conditions
        assert style == "dry and punchy drums"
        assert notes[2:5, 60].tolist() == [2, 1, 1] and drums[2, 0] == 1
        return scripted_audio(style, output=output, duration=duration)

    runtime = MRT2Runtime(tmp_path, allow_preview=True, backend_arranger=arranger, generator=generator)
    runtime.execute(manager, plan)
    assert manager.state == before and runtime.last_receipt["phase"] == "preview"
    assert (runtime.last_artifacts / "conditioning.json").is_file()
    assert not (runtime.last_artifacts / "band.mid").exists()


def test_native_mrt2_strict_mode_rejects_before_arrangement_provider(tmp_path):
    from jammate.orchestration.backend_arrangement import LLMBackendArranger

    manager, plan = staged()
    before = manager.state
    arranger = LLMBackendArranger(LLMConfig(api_key="fixture-key"), "mrt2", provider=BackendProvider({}))
    runtime = MRT2Runtime(tmp_path, backend_arranger=arranger)
    with pytest.raises(BackendCapabilityError):
        runtime.execute(manager, plan)
    assert not arranger.last_trace and manager.state == before


def test_synth_llm_writes_concrete_notes_for_authorized_voices():
    from jammate.orchestration.backend_arrangement import LLMBackendArranger, prepare_scoped_score

    manager, plan = staged()
    events = manager.current_batch()
    provider = BackendProvider({"notes": [{"instrument": "drums", "pitch": 56, "start": 0.5,
                                           "duration": .125, "velocity": 47}]})
    arranger = LLMBackendArranger(LLMConfig(api_key="fixture-key"), "synth", provider=provider)
    performance, _ = prepare_scoped_score(manager.state, plan, events, arranger)
    assert provider.context["writable_instruments"] == ["drums"]
    drums = [n for n in performance.notes if n.instrument == "drums"]
    assert [(n.pitch, n.start, n.duration) for n in drums] == [(56, .5, .125)]
    assert any(n.instrument == "bass" for n in performance.notes)


@pytest.mark.parametrize("empty_style", ["", "   "])
def test_native_mrt2_shortens_tail_spans_and_keeps_their_onsets(tmp_path, empty_style):
    from jammate.orchestration.backend_arrangement import LLMBackendArranger

    manager, plan = staged()
    before = manager.state
    provider = BackendProvider({
        "style": empty_style,
        "pitch_spans": [{"frame": 0, "length": 2, "pitch": 64}, {"frame": 238, "length": 5, "pitch": 60}],
        "drum_spans": [{"frame": 239, "length": 3}],
    })
    arranger = LLMBackendArranger(LLMConfig(api_key="fixture-key"), "mrt2", provider=provider)

    def generator(style, *, output, duration, conditions, **kwargs):
        notes, drums = conditions
        assert style == "a warm instrumental band"
        assert notes[238:, 60].tolist() == [2, 1]
        assert drums[-2:, 0].tolist() == [0, 1]
        return scripted_audio(style, output=output, duration=duration)

    runtime = MRT2Runtime(tmp_path, allow_preview=True, backend_arranger=arranger, generator=generator)
    runtime.execute(manager, plan)
    assert runtime.last_receipt["phase"] == "preview" and manager.state == before
    adjustments = runtime.last_receipt["arrangement"]["adjustments"]
    assert adjustments == {"shortened_pitch_spans": 1, "shortened_drum_spans": 1, "used_style_hint": True}
    assert provider.context["last_valid_frame"] == 239
    conditions = json.loads((runtime.last_artifacts / "conditioning.json").read_text())
    assert conditions["pitch_spans"][-1] == {"frame": 238, "length": 2, "pitch": 60}
    assert conditions["drum_spans"] == [{"frame": 239, "length": 1}]
