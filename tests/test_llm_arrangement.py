import json

import pytest

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.arrangement import LLMArranger
from jammate.orchestration.llm import ModelReply, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.session_log import SessionLog
from jammate.state import JamState


class Provider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, messages, schema, timeout_s):
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        return ModelReply(json.dumps(self.reply(context)), "test-arranger", {"output_tokens": 13})


def setup(text, provider, log, fallback="rule"):
    manager = StateManager(JamState(session_id="test", tempo_bpm=100), log=log)
    events = manager.ingest([from_text(text, session_id="test", event_id="e1")])
    plan = RuleAgent().decide(events, manager.state, plan_id="p1")
    manager.stage(plan)
    arranger = LLMArranger(LLMConfig("secret-test-token"), provider=provider, log=log, fallback=fallback)
    return manager, plan, arranger, OfflineRuntime(arranger=arranger, sample_rate=8000)


def edit(context, **params):
    return {"reason": "local fixture", "edits": [
        {"group_id": context["groups"][0]["id"], "velocity_delta": 0, "shift_steps": 0,
         "gate_percent": 100, "drop": False, **params},
    ]}


def test_model_edits_reach_rendered_notes_and_trace(tmp_path):
    log_path = tmp_path / "trace.jsonl"
    def reply(context):
        assert context["events"][0]["payload"]["text"] == "鼓更猛一点"
        assert {g["layer"] for g in context["groups"]} == {"drums"}
        return edit(context, velocity_delta=8)
    provider = Provider(reply)
    manager, plan, arranger, runtime = setup("鼓更猛一点", provider, SessionLog(log_path))
    baseline = runtime.validate(manager.state, plan)
    assert provider.calls == 0
    result = runtime.execute(manager, plan)
    assert provider.calls == 1 and arranger.last_trace["changed"]
    first_drum = next(i for i, n in enumerate(baseline.notes) if n.instrument == "drums")
    assert result.notes[first_drum].velocity == min(127, baseline.notes[first_drum].velocity + 8)
    assert [n for n in result.notes if n.instrument != "drums"] == [
        n for n in baseline.notes if n.instrument != "drums"
    ]
    assert manager.plan_status(plan.plan_id) == "applied"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    arrangement = [r for r in records if r["type"].startswith("arrangement_")]
    assert [r["type"] for r in arrangement] == ["arrangement_request", "arrangement_model_output", "arrangement_result"]
    assert all(r["plan_id"] == plan.plan_id for r in arrangement)
    assert arrangement[-1]["note_output"][first_drum]["velocity"] == result.notes[first_drum].velocity
    assert "secret-test-token" not in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("invalid", [
    {"group_id": "g9999"}, {"shift_steps": 1}, {"velocity_delta": -8},
])
def test_out_of_scope_or_contradictory_edit_keeps_entire_rule_score(invalid):
    provider = Provider(lambda context: edit(context, **invalid))
    manager, plan, arranger, runtime = setup("鼓更猛一点", provider, None)
    baseline = runtime.validate(manager.state, plan)
    assert runtime.execute(manager, plan).notes == baseline.notes
    assert arranger.last_trace["status"] == "fallback"


def test_preserve_only_does_not_call_arranger():
    provider = Provider(lambda _: pytest.fail("preserve-only turns need no model"))
    manager, plan, arranger, runtime = setup("就这个", provider, None)
    baseline = runtime.validate(manager.state, plan)
    assert runtime.execute(manager, plan).notes == baseline.notes
    assert provider.calls == 0 and arranger.last_trace["status"] == "skipped"


def test_strict_arrangement_failure_does_not_commit_plan(tmp_path):
    def timeout(_):
        raise ProviderFailure("timeout")
    path = tmp_path / "strict.jsonl"
    manager, plan, arranger, runtime = setup("鼓更猛一点", Provider(timeout), SessionLog(path), fallback="error")
    before = manager.state
    with pytest.raises(PlanError, match="timeout"):
        runtime.execute(manager, plan)
    assert manager.state == before and manager.current_batch()
    assert manager.plan_status(plan.plan_id) == "failed"
    assert arranger.last_trace["status"] == "failed"
    assert '"note_output": null' in path.read_text(encoding="utf-8")
