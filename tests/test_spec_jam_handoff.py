"""Product scenario: User Stalls and Call & Response on one playback clock."""
from dataclasses import replace
import json
from math import ceil
from pathlib import Path

import pytest

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_humming, from_system
from jammate.interaction import Strategy
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.plans import make_plan
from jammate.orchestration.realtime import RealtimeSession
from jammate.state import JamState


SESSION_ID = "golden-call-response"
BAR_SAMPLES = 2500  # 96 BPM in 4/4 at 1000 samples per second.
HUMMING = Path(__file__).resolve().parents[1] / "examples/input_events/source/humming_melody_v1.json"


def lifecycle(command, event_id, when, **details):
    return from_system(command, session_id=SESSION_ID, event_id=event_id, occurred_at=when, **details)


def motif_action(action, motif_id):
    return {"action": action, "scope": "lead", "strength": .5, "parameters": {"motif_id": motif_id}}


class ScriptedDirector:
    def complete(self, messages, _schema, _timeout):
        context = json.loads(messages[1]["content"])
        first = context["events"][0]
        interaction = context["state"]["interaction"]
        strategy, actions = "LISTEN", []
        if first["event_id"] == "first-idle":
            assert not interaction["awaiting_user"]
            strategy = "INITIATE"
            actions = [motif_action("INTRODUCE_MOTIF", context["new_motif_id"])]
        elif first["event_id"] == "repeat-idle":
            assert interaction["awaiting_user"]
        elif first["event_id"] == "humming-start":
            assert interaction["user_active"]
        else:
            assert first["event_id"] == "hummed-reply" and not interaction["user_active"]
            strategy = "RESPOND"
            actions = [motif_action("ECHO_MOTIF", first["registered_motif_id"])]
        return ModelReply(json.dumps({
            "strategy": strategy, "actions": actions, "preserve": ["tempo", "key"],
            "transport": {"tempo_bpm": None, "playing": None}, "valid_for_bars": 4,
            "reason": "Golden scenario fixture", "unresolved": [],
        }), "scripted", {})


def lead_notes(bars):
    return tuple(note for bar in bars for note in bar.notes if note.instrument == "lead")


@pytest.mark.parametrize("agent_type", ["rule", "llm_adapter"])
def test_idle_handoff_and_humming_response_share_one_live_session(agent_type):
    manager = StateManager(JamState(session_id=SESSION_ID, tempo_bpm=96))
    runtime = RealtimeSession(manager, sample_rate=1000)
    agent = (RuleAgent() if agent_type == "rule" else
             LLMAgent(LLMConfig(api_key="scripted"), provider=ScriptedDirector(), fallback="error"))
    heard = list(runtime.advance_to(0))

    def advance(sample):
        bars = runtime.advance_to(sample)
        heard.extend(bars)
        return bars

    def queue(request):
        change = runtime.bind(request, request.decide(agent)).prepare()
        assert runtime.schedule(change)
        assert manager.plan_status(change.decision.plan.plan_id) == "pending"
        return change

    advance(2 * BAR_SAMPLES)
    first = runtime.observe((lifecycle("idle", "first-idle", 5, bars=2),))
    idea = queue(first)
    intro_plan = idea.decision.plan.to_dict()
    assert intro_plan["strategy"] == "INITIATE"
    assert [action["action"] for action in intro_plan["actions"]] == ["INTRODUCE_MOTIF"]
    assert not manager.state.interaction.awaiting_user
    (opening,) = advance(3 * BAR_SAMPLES)
    assert opening.started.plan_id == intro_plan["plan_id"]
    assert manager.state.interaction.strategy is Strategy.LISTEN
    assert manager.state.interaction.awaiting_user
    advance(5 * BAR_SAMPLES)
    idea_notes = tuple(note.shifted(opening.timing.bar_index * 4) for note in idea.performance.lead_notes)
    assert idea_notes and lead_notes(heard) == idea_notes

    repeat = runtime.observe((lifecycle("idle", "repeat-idle", 12.5, bars=2),))
    wait = queue(repeat)
    assert wait.decision.plan.to_dict()["strategy"] == "LISTEN"
    assert not wait.performance.lead_notes
    (space,) = advance(6 * BAR_SAMPLES)
    assert space.notes and not lead_notes((space,))
    assert manager.state.interaction.awaiting_user
    assert len(manager.state.motifs) == 1

    active = runtime.observe((lifecycle("user_activity", "humming-start", 15, active=True),))
    assert active.state.interaction.user_active
    illegal = make_plan(active.state, active.events, "would-interrupt", "INITIATE",
                        [motif_action("INTRODUCE_MOTIF", "motif_would_interrupt")], [], {}, 4,
                        "Disallowed competing lead", [])
    with pytest.raises(PlanError, match="user_active"):
        runtime.bind(active, illegal).prepare()
    listening = queue(active)
    assert listening.decision.plan.to_dict()["strategy"] == "LISTEN"
    assert not listening.performance.lead_notes
    advance(7 * BAR_SAMPLES)

    melody = json.loads(HUMMING.read_text(encoding="utf-8"))
    phrase = from_humming(melody, session_id=SESSION_ID, event_id="hummed-reply", started_at=15)
    advance(ceil(phrase.ended_at * runtime.sample_rate))
    assert lead_notes(heard) == idea_notes
    finish = lifecycle("user_activity", "humming-end", phrase.ended_at, active=False)
    response = runtime.observe((phrase, finish))
    assert not response.state.interaction.user_active
    echo = queue(response)
    echo_plan = echo.decision.plan.to_dict()
    assert echo_plan["strategy"] == "RESPOND"
    assert echo_plan["trigger_event_ids"] == ["hummed-reply", "humming-end"]
    assert [(a["action"], a["parameters"]["motif_id"]) for a in echo_plan["actions"]] == [
        ("ECHO_MOTIF", "motif_hummed-reply")]

    echo_start = echo.decision.slot.start_sample
    rendered_echo = advance(echo_start + 3 * BAR_SAMPLES)
    motif = manager.state.motif("motif_hummed-reply")
    expected = tuple(replace(note, instrument="lead").shifted(echo.decision.slot.bar * 4)
                     for note in motif.notes)
    actual_echo = lead_notes(rendered_echo)
    assert sorted(actual_echo, key=lambda n: (n.start, n.pitch)) == sorted(expected, key=lambda n: (n.start, n.pitch))
    assert rendered_echo[0].started.plan_id == echo_plan["plan_id"]
    assert manager.state.current_motif_id == motif.motif_id
    assert not manager.state.interaction.awaiting_user
    assert manager.state.interaction.strategy is Strategy.LISTEN
    assert all(bar.notes and bar.timing.tempo_bpm == 96 for bar in heard)
    assert all(left.timing.end_sample == right.timing.start_sample for left, right in zip(heard, heard[1:]))
