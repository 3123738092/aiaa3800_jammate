"""Validate the direct-generator boundary without calling a live provider."""
import json

import pytest

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.bass_generation import LLMBassArranger
from jammate.orchestration.llm import ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime, prepare_plan
from jammate.orchestration.plans import make_plan
from jammate.rhythm import RhythmContext
from jammate.state import JamState


def fixture(preserve_bass=False):
    manager = StateManager(JamState(session_id="bass", tempo_bpm=100, energy=2,
                                    rhythm=RhythmContext(100, (0., 1.5, 3.), (1., .6, .8))))
    batch = manager.ingest([from_text("贝斯都弹根音", session_id="bass", event_id="e")])
    keep = ["tempo", "key", "groove", "layer:drums", "layer:keys"]
    if preserve_bass:
        keep.append("layer:bass")
    plan = make_plan(manager.state, batch, "p", "FOLLOW",
                     [{"action": "ADJUST_FEEL", "scope": "bass", "strength": .25, "parameters": {"level_delta": 0}}],
                     keep, {}, 2, "experimental bass", [])
    manager.stage(plan)
    return manager, batch, plan


class ScriptedProvider:
    def __init__(self, modify=lambda notes: None):
        self.calls = 0
        self.modify = modify

    def complete(self, messages, schema, timeout_s):
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        assert set(context) == {"band_plan", "session_bpm", "key_pitch_classes", "bars",
                                "allow_extra_onsets", "lead", "events"}
        assert context["events"][0]["payload"]["text"] == "贝斯都弹根音"
        notes = [{"bar": b["bar"], "step": s, "length_steps": 2,
                  "pitch": 48 + b["chord_root_pc"], "velocity": 80}
                 for b in context["bars"] for s in b["anchor_steps"]]
        self.modify(notes)
        return ModelReply(json.dumps({"notes": notes, "reason": "test fixture"}), "scripted", {})


def test_offline_runtime_executes_direct_bass_and_other_parts_remain_exact():
    manager, _, plan = fixture()
    baseline = prepare_plan(manager.state, plan)
    provider = ScriptedProvider()
    arranger = LLMBassArranger(LLMConfig("local-fixture"), provider=provider, fallback="error")
    runtime = OfflineRuntime(arranger=arranger, sample_rate=8000)
    runtime.validate(manager.state, plan)
    assert provider.calls == 0
    output = runtime.execute(manager, plan)
    assert provider.calls == 1
    assert manager.plan_status(plan.plan_id) == "applied"
    assert [n for n in output.notes if n.instrument != "bass"] == [n for n in baseline.notes if n.instrument != "bass"]
    bass = [n for n in output.notes if n.instrument == "bass"]
    assert [n.pitch for n in bass] == [48, 48, 48, 55, 55, 55]
    assert [n.start for n in bass] == [0, 1.5, 3, 4, 5.5, 7]
    assert all(n.duration == .5 for n in bass)


@pytest.mark.parametrize("change, error", [
    (lambda notes: notes.pop(0), "anchors"),
    (lambda notes: notes[0].update(pitch=49), "pitch"),
    (lambda notes: notes[0].update(length_steps=7), "overlap"),
    (lambda notes: notes.append({**notes[-1], "step": 15, "length_steps": 1}), "anchors"),
])
def test_invalid_score_is_rejected_and_plan_has_no_applied_state(change, error):
    manager, _, plan = fixture()
    before = manager.state
    arranger = LLMBassArranger(LLMConfig("local-fixture"), provider=ScriptedProvider(change), fallback="error")
    with pytest.raises(PlanError, match=error):
        OfflineRuntime(arranger=arranger, sample_rate=8000).execute(manager, plan)
    assert manager.state == before and manager.plan_status(plan.plan_id) == "failed"


def test_preserved_bass_returns_rule_reference_without_model_call():
    manager, batch, plan = fixture(preserve_bass=True)
    baseline = prepare_plan(manager.state, plan)
    provider = ScriptedProvider()
    arranger = LLMBassArranger(LLMConfig("local-fixture"), provider=provider)
    assert arranger.arrange(manager.state, plan, baseline, batch) is baseline
    assert provider.calls == 0 and arranger.last_trace["status"] == "fallback"


def test_direct_bass_for_text_opening_without_a_rhythm_seed():
    manager = StateManager(JamState(session_id="bass", tempo_bpm=100))
    batch = manager.ingest([from_text("来一段，慢一点", session_id="bass", event_id="e")])
    plan = RuleAgent().decide(batch, manager.state, plan_id="opening", direct_bass_enabled=True)
    baseline = prepare_plan(manager.state, plan)

    class OpeningProvider:
        def complete(self, messages, schema, timeout_s):
            context = json.loads(messages[-1]["content"])
            assert context["session_bpm"] == 92
            assert context["allow_extra_onsets"]
            assert len(context["bars"]) == 4
            assert all(not b["anchor_steps"] for b in context["bars"])
            notes = [{"bar": b["bar"], "step": 0, "length_steps": 8,
                      "pitch": 48 + b["chord_root_pc"], "velocity": 80} for b in context["bars"]]
            return ModelReply(json.dumps({"notes": notes, "reason": "one root per bar"}), "scripted", {})

    arranger = LLMBassArranger(LLMConfig("local-fixture"), provider=OpeningProvider(), fallback="error")
    manager.stage(plan)
    output = OfflineRuntime(arranger=arranger, sample_rate=8000).execute(manager, plan)
    assert [n.start for n in output.notes if n.instrument == "bass"] == [0., 4., 8., 12.]
    assert [n for n in output.notes if n.instrument != "bass"] == [n for n in baseline.notes if n.instrument != "bass"]
    assert manager.state.position.next_bar == 4
