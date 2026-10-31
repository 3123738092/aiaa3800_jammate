"""Multi-turn failure injection; no provider calls or physical audio devices."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.interaction import Motif, RhythmSource
from jammate.music import Note
from jammate.rhythm import RhythmContext
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, build_context, decision_schema
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.state import JamState
from renderer_experiments.app import band_plan as backend
from test_kevin_runtime import fake_render, runtime, staged
from test_llm_agent import FakeProvider, decision


def ack(manager, plan, success=True):
    d = plan.to_dict()
    return manager.acknowledge(plan, succeeded=success, effective_bar=d["effective_at"]["bar"],
                               completed_bar=d["effective_at"]["bar"] + d["valid_for_bars"])


class BrokenLog:
    def write(self, *_args, **_kwargs):
        raise OSError(28, "injected disk full")


def test_log_failure_does_not_consume_input_or_lose_receipt():
    m = StateManager(JamState(session_id="log_test"), log=BrokenLog())
    event = from_text("鼓更猛", session_id="log_test", event_id="e1")
    events = m.ingest([event])
    plan = RuleAgent().decide(events, m.state, plan_id="p1")
    m.stage(plan)
    assert ack(m, plan)
    assert m.state.state_version == 2 and m.plan_status("p1") == "applied"
    assert not m.ingest([event])
    assert not ack(m, plan)
    assert m.diagnostics()["log_write_failures"] > 0


def test_failed_export_can_replan_same_input_without_new_input_id():
    m, plan = staged()
    events = m.current_batch()
    before = m.state
    ack(m, plan, False)
    assert m.state == before
    assert m.current_batch() == events
    _, history = m.decision_snapshot()
    assert history[-1]["plans"][0]["status"] == "failed"
    retry = RuleAgent().decide(events, m.state, plan_id="retry")
    m.stage(retry)
    assert ack(m, retry)
    assert not m.current_batch()
    assert m.state.layer("drums").level == pytest.approx(.75)
    assert not ack(m, retry)


def test_rejected_concurrent_receipt_is_not_reported_as_success(tmp_path, monkeypatch):
    manager, plan = staged()
    def reject_during_export(*args):
        fake_render(*args)
        ack(manager, plan, False)
    monkeypatch.setattr(backend, "render_with_fluidsynth", reject_during_export)
    r = runtime(tmp_path)
    with pytest.raises(PlanError):
        r.execute(manager, plan)
    assert manager.plan_status(plan.plan_id) == "failed"
    receipt = json.loads((r.last_artifacts / "receipt.json").read_text())
    assert not receipt["succeeded"]


def test_reference_renderer_rejects_a_noop_terminal_receipt(monkeypatch):
    from jammate.orchestration import offline
    manager, plan = staged()
    original = offline.render_plan
    def fail_during_render(*args, **kwargs):
        result = original(*args, **kwargs)
        ack(manager, plan, False)
        return result
    monkeypatch.setattr(offline, "render_plan", fail_during_render)
    with pytest.raises(PlanError):
        offline.OfflineRuntime(sample_rate=8000).execute(manager, plan)
    assert manager.plan_status(plan.plan_id) == "failed"


def test_receipt_file_failure_after_commit_is_explicit_and_not_reexecuted(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "render_with_fluidsynth", fake_render)
    manager, plan = staged()
    original = backend._write
    def fail_receipt(path, value):
        if path.name == "receipt.json":
            raise OSError(28, "injected disk full after commit")
        original(path, value)
    monkeypatch.setattr(backend, "_write", fail_receipt)
    r = runtime(tmp_path)
    result = r.execute(manager, plan)
    assert result.notes and manager.plan_status(plan.plan_id) == "applied"
    assert r.last_receipt["succeeded"]
    assert r.last_receipt["persistence_warnings"]
    with pytest.raises(PlanError):
        r.execute(manager, plan)


def test_receipt_snapshot_does_not_include_a_later_concurrent_input(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "render_with_fluidsynth", fake_render)
    manager, plan = staged()
    original = manager.acknowledge_and_snapshot
    def accept_then_input(*args, **kwargs):
        accepted, snapshot = original(*args, **kwargs)
        manager.ingest([from_text("下一轮", session_id="kevin_test", event_id="after_commit")])
        return accepted, snapshot
    monkeypatch.setattr(manager, "acknowledge_and_snapshot", accept_then_input)
    r = runtime(tmp_path)
    r.execute(manager, plan)
    applied = json.loads((r.last_artifacts / "state_applied.json").read_text(encoding="utf-8"))
    assert manager.state.state_version == 3
    assert applied["state_version"] == r.last_receipt["state_version_after"] == 2
    assert applied["interaction"]["last_feedback"] != manager.state.interaction.last_feedback


def test_existing_artifact_directory_does_not_fail_another_inflight_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "render_with_fluidsynth", fake_render)
    manager, plan = staged()
    r = runtime(tmp_path)
    import hashlib
    digest = hashlib.sha256((manager.state.session_id + ":" + plan.plan_id).encode()).hexdigest()[:20]
    (r.output / f"plan_{digest}").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        r.execute(manager, plan)
    assert manager.plan_status(plan.plan_id) == "pending"


def long_state(count):
    notes = tuple(Note(60 + i % 5, i * .25, .2, 80, "user") for i in range(16))
    return JamState(session_id="long", current_motif_id="m0",
        motifs=tuple(Motif(f"m{i}", "user", (f"old{i}",), notes) for i in range(count)),
        rhythm_sources=tuple(RhythmSource(f"r{i}", RhythmContext(100, (0., 1., 2.), (1., .7, .8), 0.)) for i in range(count)))


def test_context_and_schema_stop_growing_but_authoritative_state_is_lossless():
    sizes = []
    for count in (32, 128, 512):
        state = long_state(count)
        event = from_text("保持当前旋律和节奏。", session_id="long", event_id="new")
        provider = FakeProvider(decision(actions=[], preserve=["tempo", "key"]))
        agent = LLMAgent(LLMConfig("test-only"), provider=provider)
        agent.decide([event], state, plan_id="p1")
        assert agent.last_trace["source"] == "llm"
        call = provider.calls[0]
        context = json.loads(call["messages"][1]["content"])
        assert "m0" in {m["motif_id"] for m in context["state"]["motifs"]["items"]}
        assert context["context_selection"]["motifs_omitted"] > 0
        assert len(state.motifs) == count and state.motif("m1").notes
        sizes.append(len(json.dumps(call, ensure_ascii=False)))
        for branch in call["schema"]["properties"]["actions"]["items"]["anyOf"]:
            if branch["properties"]["action"]["enum"][0] == "ECHO_MOTIF":
                allowed = branch["properties"]["parameters"]["properties"]["motif_id"]["enum"]
                assert len(allowed) <= 12 and "m0" in allowed
    assert max(sizes) - min(sizes) < 2000


def test_request_budget_includes_schema_and_repair_transcript():
    event = from_text("保持", session_id="long", event_id="new")
    state = JamState(session_id="long")
    provider = FakeProvider(decision())
    agent = LLMAgent(LLMConfig("test-only"), provider=provider, max_context_chars=4000)
    agent.decide([event], state, plan_id="p1")
    assert not provider.calls
    assert agent.last_trace["failure"] == "context_too_large"


def test_repair_is_not_sent_when_it_would_exceed_budget():
    event = from_text("保持", session_id="long", event_id="new")
    provider = FakeProvider("{" + " " * 30000)
    agent = LLMAgent(LLMConfig("test-only"), provider=provider, max_context_chars=25000)
    agent.decide([event], JamState(session_id="long"))
    assert len(provider.calls) == 1
    assert agent.last_trace["failure"] == "context_too_large"


def test_new_input_supersedes_a_failed_batch_and_blocks_old_retry():
    manager, plan = staged()
    old_events = manager.current_batch()
    ack(manager, plan, False)
    manager.ingest([from_text("停止", session_id="kevin_test", event_id="new")])
    retry = RuleAgent().decide(old_events, manager.state, plan_id="obsolete_retry")
    with pytest.raises(PlanError):
        manager.stage(retry)
    assert [e.event_id for e in manager.current_batch()] == ["new"]


def test_atomic_json_replacement_does_not_damage_old_evidence(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    backend._write(path, {"first": True})
    def disk_full(*_):
        raise OSError(28, "injected replace failure")
    monkeypatch.setattr(backend.os, "replace", disk_full)
    with pytest.raises(OSError):
        backend._write(path, {"second": True})
    assert json.loads(path.read_text()) == {"first": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_oversized_current_input_is_not_silently_truncated():
    event = from_text("请保持" * 40000, session_id="long", event_id="new")
    provider = FakeProvider()
    agent = LLMAgent(LLMConfig("test-only"), provider=provider)
    plan = agent.decide([event], JamState(session_id="long"))
    assert not provider.calls and agent.last_trace["source"] == "fallback"
    assert "context_too_large" in plan.to_dict()["unresolved"][0]


def test_old_explicit_motif_id_is_available_without_adding_all_old_notes():
    state = long_state(128)
    event = from_text("重复 m3", session_id="long", event_id="new")
    context = build_context([event], state, "p1")
    ids = {m["motif_id"] for m in context["state"]["motifs"]["items"]}
    assert {"m0", "m3"} <= ids and len(ids) <= 12


def test_transport_and_state_remain_monotonic_across_failures_duplicates_and_replacements():
    m = StateManager(JamState(session_id="soak"))
    version, bar = 0, 0
    for i in range(120):
        event = from_text("保持", session_id="soak", event_id=f"e{i}", started_at=i, ended_at=i)
        events = m.ingest([event])
        assert not m.ingest([event])
        first = RuleAgent().decide(events, m.state, plan_id=f"p{i}")
        m.stage(first)
        if i % 7 == 0:
            ack(m, first, False)
            plan = RuleAgent().decide(m.current_batch(), m.state, plan_id=f"retry{i}")
            m.stage(plan)
        elif i % 11 == 0:
            plan = RuleAgent().decide(events, m.state, plan_id=f"replacement{i}")
            m.stage(plan)
            with pytest.raises(PlanError):
                ack(m, first)
        else:
            plan = first
        ack(m, plan)
        assert not ack(m, plan)
        assert m.state.state_version == version + 2
        assert m.state.position.next_bar > bar
        assert m.state.layers == JamState().layers
        version, bar = m.state.state_version, m.state.position.next_bar
