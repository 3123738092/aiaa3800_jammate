"""Deterministic protocol tests; ordinary pytest never calls a real API."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from jammate.contracts import PlanError, load_input_event
from jammate.contracts.adapters import from_text, from_system
from jammate.interaction import Interaction, Motif
from jammate.music import Note
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ModelReply, OpenAIProvider, ProviderFailure, build_context, decision_schema
from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.state import JamState

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-only-secret-never-a-real-key"


def action(name="BUILD", scope="drums", **params):
    return {"action": name, "scope": scope, "strength": .4, "parameters": params or ({"duration_bars": 4} if name in {"BUILD", "SIMPLIFY"} else {})}


def decision(**changes):
    return {"strategy": "FOLLOW", "valid_for_bars": 4, "preserve": ["tempo", "key", "layer:bass", "layer:keys"],
            "actions": [action()], "transport": {"playing": None, "tempo_bpm": None}, "reason": "只增强鼓", "unresolved": [], **changes}


class FakeProvider:
    def __init__(self, *replies, callback=None):
        self.replies = list(replies)
        self.calls = []
        self.callback = callback

    def complete(self, messages, schema, timeout_s):
        self.calls.append(json.loads(json.dumps({"messages": messages, "schema": schema, "timeout": timeout_s})))
        if self.callback:
            self.callback()
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return ModelReply(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False), "fake-model", {"input_tokens": 100, "output_tokens": 50})


def setup(*replies, **state_kwargs):
    m = StateManager(JamState(session_id="jam_demo", tempo_bpm=100, **state_kwargs))
    events = m.ingest([from_text("不要加速，让鼓像是在推着大家前进，和弦与贝斯照旧。", session_id="jam_demo", event_id="e1")])
    fake = FakeProvider(*replies)
    return m, events, LLMAgent(LLMConfig(SECRET), provider=fake), fake


def test_llm_is_primary_not_a_keyword_preparser(monkeypatch):
    m, events, agent, fake = setup(decision())
    monkeypatch.setattr(RuleAgent, "decide", lambda *a, **k: pytest.fail("rule parser must not run"))
    plan = agent.decide(events, m.state, plan_id="p1")
    assert plan.actions[0]["action"] == "BUILD" and agent.last_trace["source"] == "llm"
    assert plan.based_on_state_version == 1 and plan.to_dict()["trigger_event_ids"] == ["e1"]
    assert events[0].payload["text"] in fake.calls[0]["messages"][1]["content"]
    assert SECRET not in json.dumps(fake.calls) + json.dumps(agent.last_trace)
    m.stage(plan)
    OfflineRuntime(sample_rate=8000).execute(m, plan)
    assert m.state.layer("drums").level == pytest.approx(.7)
    assert m.state.layer("bass").level == .5 and m.state.tempo_bpm == 100


def test_schema_requires_every_branch_field_and_has_no_backend_notes():
    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            assert "notes" not in schema["properties"]
        for value in schema.values():
            if isinstance(value, dict):
                check(value)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, dict):
                        check(child)
    check(decision_schema())


def test_current_turn_follows_history_and_preserve_choices_match_state():
    m, events, _, _ = setup()
    context = build_context(events, m.state, "p1")
    assert list(context)[-1] == "events"
    schema = decision_schema(m.state, "p1")
    assert "band" not in schema["properties"]["preserve"]["items"]["enum"]
    assert not any(x.startswith("motif:") for x in schema["properties"]["preserve"]["items"]["enum"])
    names = [b["properties"]["action"]["enum"][0] for b in schema["properties"]["actions"]["items"]["anyOf"]]
    assert "ECHO_MOTIF" not in names and "INTRODUCE_MOTIF" in names


def test_actual_renderer_preservation_failure_is_repaired_before_staging():
    m = StateManager(JamState(session_id="jam_demo", tempo_bpm=100))
    events = m.ingest([load_input_event(ROOT / "examples/input_events/rhythm.json")])
    rhythm_id = events[0].event_id
    bad = decision(actions=[action("ADJUST_FEEL", "band", rhythm_event_id=rhythm_id, level_delta=None)])
    good = decision(actions=[action("ADJUST_FEEL", "band", rhythm_event_id=rhythm_id, level_delta=None)], preserve=["tempo", "key"])
    fake = FakeProvider(bad, good)
    agent = LLMAgent(LLMConfig(SECRET), provider=fake)
    plan = agent.decide(events, m.state)
    assert len(fake.calls) == 2 and "preserved layer" in agent.last_trace["attempts"][0]["validation_error"]
    assert m.state.rhythm is None
    m.stage(plan)
    OfflineRuntime(sample_rate=8000).execute(m, plan)
    assert m.state.rhythm is not None


def test_subtle_simplification_removes_real_notes_in_sparse_accompaniment():
    result = decision(actions=[{**action("SIMPLIFY", "drums"), "strength": .2}])
    m, events, agent, _ = setup(result)
    plan = agent.decide(events, m.state)
    from jammate.orchestration.offline import prepare_plan
    baseline = RuleAgent._plan(m.state, events, "baseline", "FOLLOW", [], [], {}, 4, "baseline", [])
    old_notes, new_notes = prepare_plan(m.state, baseline).notes, prepare_plan(m.state, plan).notes
    assert len(new_notes) < len(old_notes)
    assert tuple(n for n in new_notes if n.instrument != "drums") == tuple(n for n in old_notes if n.instrument != "drums")


@pytest.mark.parametrize("bad", [
    "not JSON", "```json\n{}\n```", '{"strategy":"FOLLOW","strategy":"LISTEN"}',
    decision(actions=[action("BUILD", "guitar", duration_bars=4)]),
    decision(actions=[action("PRESERVE", notes=[60])]),
    decision(transport={"playing": None, "tempo_bpm": 120}),
    decision(actions=[action("ECHO_MOTIF", "lead", motif_id="missing")]),
    decision(preserve=["groove"], actions=[action("SIMPLIFY", "drums", duration_bars=4)]),
    decision(actions=[action(), action()]),
    decision(actions=[action(strength=float("nan"))]),
    {**decision(), "based_on_state_version": 1000},
])
def test_invalid_proposals_get_one_repair_before_staging(bad):
    m, events, agent, fake = setup(bad, decision())
    before = m.state
    plan = agent.decide(events, before, plan_id="p1")
    assert len(fake.calls) == 2 and m.state == before
    assert agent.last_trace["attempts"][0]["status"] == "invalid_output"
    assert agent.last_trace["attempts"][1]["status"] == "accepted"
    assert "original state is unchanged" in fake.calls[1]["messages"][-1]["content"]
    m.stage(plan)


@pytest.mark.parametrize("failure", ["timeout", "authentication", "rate_limit", "connection", "refusal", "incomplete_output"])
def test_network_refusal_and_quota_failures_hold_without_blind_retries(failure):
    m, events, agent, fake = setup(ProviderFailure(failure))
    plan = agent.decide(events, m.state, plan_id="p1")
    assert len(fake.calls) == 1 and not plan.actions and not plan.to_dict()["transport"]
    assert plan.to_dict()["unresolved"] == [f"llm_unavailable:{failure}"]
    assert agent.last_trace["source"] == "fallback" and agent.last_trace["fallback"] == "hold"


def test_two_invalid_replies_are_not_reported_as_a_successful_llm_plan():
    m, events, agent, fake = setup("bad", "still bad")
    plan = agent.decide(events, m.state)
    assert len(fake.calls) == 2 and not plan.actions and plan.to_dict()["unresolved"]
    assert agent.last_trace["source"] == "fallback"


def test_context_budget_never_silently_truncates_the_current_input():
    m, events, agent, fake = setup()
    agent.max_context_chars = 20
    plan = agent.decide(events, m.state)
    assert not fake.calls and "context_too_large" in plan.to_dict()["unresolved"][0]


def test_new_input_during_network_call_rejects_old_result():
    m, events, agent, fake = setup(decision())
    fake.callback = lambda: m.ingest([from_text("停止", session_id="jam_demo", event_id="e2")])
    old_snapshot = m.state
    plan = agent.decide(events, old_snapshot, plan_id="old")
    with pytest.raises(PlanError):
        m.stage(plan)
    assert m.state.state_version == 2 and m.state.layer("drums").level == .5


def test_system_stop_never_waits_for_the_model():
    m, _, agent, fake = setup()
    events = m.ingest([from_system("stop", session_id="jam_demo", event_id="stop")])
    plan = agent.decide(events, m.state)
    assert not fake.calls and plan.to_dict()["transport"] == {"playing": False}
    assert agent.last_trace["source"] == "system_control"


@pytest.mark.parametrize("state_kwargs,event", [
    ({"interaction": Interaction(user_active=True)}, "idle"),
    ({"interaction": Interaction(awaiting_user=True)}, "idle"),
    ({"playing": False}, "idle"),
])
def test_ownership_and_restart_guards_are_independent_of_prompt(state_kwargs, event):
    m = StateManager(JamState(session_id="jam_demo", tempo_bpm=100, **state_kwargs))
    fake = FakeProvider()
    agent = LLMAgent(LLMConfig(SECRET), provider=fake)
    events = m.ingest([from_system(event, session_id="jam_demo", event_id="idle", bars=2)])
    fake.replies = [decision(strategy="INITIATE", actions=[action("INTRODUCE_MOTIF", "lead", motif_id="motif_p1")],
                            transport={"playing": True, "tempo_bpm": None}), decision(actions=[], strategy="LISTEN")]
    plan = agent.decide(events, m.state, plan_id="p1")
    assert not plan.actions and len(fake.calls) == 2


def test_motif_must_fit_and_must_use_registered_identity():
    motif = Motif("theme", "user", ("seed",), (Note(60, 15, 2),))
    m, events, agent, fake = setup(motifs=(motif,), current_motif_id="theme")
    fake.replies = [decision(actions=[action("ECHO_MOTIF", "lead", motif_id="theme")]),
                    decision(actions=[action("ECHO_MOTIF", "lead", motif_id="theme")], valid_for_bars=5)]
    plan = agent.decide(events, m.state)
    assert plan.to_dict()["valid_for_bars"] == 5 and len(fake.calls) == 2


def test_context_has_raw_language_music_and_history_without_opaque_provenance():
    m = StateManager(JamState(session_id="jam_demo", tempo_bpm=100))
    humming = load_input_event(ROOT / "examples/input_events/humming.json")
    events = m.ingest([humming])
    context = build_context(events, m.state, "p1")
    evidence = context["events"][0]
    assert evidence["payload"]["notes"] == humming.payload["melody"]["notes"]
    assert "results" not in evidence["payload"] and "provenance" not in evidence
    assert context["state"]["motifs"]["items"] and context["new_motif_id"] == "motif_p1"


def test_history_tracks_applied_and_failed_changes_without_claiming_execution():
    m, events, agent, fake = setup(decision())
    first = agent.decide(events, m.state, plan_id="p1")
    m.stage(first)
    OfflineRuntime(sample_rate=8000).execute(m, first)
    events2 = m.ingest([from_text("鼓再轻一些", session_id="jam_demo", event_id="e2")])
    second = RuleAgent().decide(events2, m.state, plan_id="p2")
    m.stage(second)
    m.acknowledge(second, succeeded=False, effective_bar=4, completed_bar=5, reason="test failure")
    events3 = m.ingest([from_text("回到最开始", session_id="jam_demo", event_id="e3")])
    state, history = m.decision_snapshot()
    assert len(history) == 2 and history[0]["plans"][0]["status"] == "applied"
    assert history[0]["state_before"]["arrangement"]["layers"][0]["level"] == .5
    assert history[0]["state_after"]["arrangement"]["layers"][0]["level"] == pytest.approx(.7)
    assert history[1]["plans"][0]["status"] == "failed" and history[1]["state_after"] is None
    history[0]["state_before"]["arrangement"]["layers"][0]["level"] = 0
    assert m.decision_snapshot()[1][0]["state_before"]["arrangement"]["layers"][0]["level"] == .5
    assert len(build_context(events3, state, "p3", history)["recent_turns"]) == 2


def test_error_fallback_is_explicit_for_evaluations():
    m, events, agent, _ = setup(ProviderFailure("timeout"))
    agent.fallback = "error"
    with pytest.raises(ProviderFailure, match="timeout"):
        agent.decide(events, m.state)


def test_private_config_and_env_override_never_repr_the_key(tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"api_key": SECRET, "model": "file-model", "base_url": "https://provider.invalid/v1"}))
    config = LLMConfig.load(path, environ={})
    assert config.model == "file-model" and SECRET not in repr(config) + json.dumps(config.public_info())
    env = {"OPENAI_API_KEY": "env-secret", "JAMMATE_LLM_MODEL": "env-model"}
    config = LLMConfig.load(path, environ=env)
    assert config.api_key == "env-secret" and config.model == "env-model"
    assert config.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize("changes", [
    {"api_key": ""}, {"base_url": "http://example.com/v1"}, {"base_url": "https://a.test/v1?key=secret"},
    {"api": "guess"}, {"timeout_s": 0}, {"timeout_s": float("nan")}, {"max_output_tokens": True},
])
def test_bad_config_is_rejected_before_any_request(changes):
    with pytest.raises(LLMConfigurationError):
        LLMConfig(**{"api_key": SECRET, **changes})


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
def test_provider_sends_strict_schema_store_false_and_key_only_in_header(api):
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        if api == "responses":
            return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 0, "status": "completed", "model": "fake",
                "output": [{"type": "message", "id": "msg", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(decision()), "annotations": []}]}],
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}})
        return httpx.Response(200, json={"id": "chat_test", "object": "chat.completion", "created": 0, "model": "fake",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(decision())}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = openai.OpenAI(api_key=SECRET, base_url="https://example.invalid/v1", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        config = LLMConfig(SECRET, api=api, reasoning_effort="low")
        reply = OpenAIProvider(config, client=client).complete([{"role": "user", "content": "JSON music decision"}], decision_schema(), 3.)
        assert json.loads(reply.text)["strategy"] == "FOLLOW"
        body = json.loads(calls[0].content)
        assert body["store"] is False and SECRET not in calls[0].content.decode()
        assert (body["reasoning"]["effort"] if api == "responses" else body["reasoning_effort"]) == "low"
        format_spec = body["text"]["format"] if api == "responses" else body["response_format"]["json_schema"]
        assert format_spec["strict"] is True
    finally:
        client.close()


def test_provider_error_body_with_secret_is_not_logged_or_retried():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": f"bad key {SECRET}", "type": "invalid_request_error"}})
    client = openai.OpenAI(api_key=SECRET, base_url="https://example.invalid/v1", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        m, events, agent, _ = setup()
        agent.provider = OpenAIProvider(agent.config, client=client)
        plan = agent.decide(events, m.state)
        assert len(calls) == 1 and agent.last_trace["failure"] == "authentication"
        assert SECRET not in json.dumps(plan.to_dict()) + json.dumps(agent.last_trace)
    finally:
        client.close()
