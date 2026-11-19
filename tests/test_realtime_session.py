from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import numpy as np
import pytest

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_system, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_bar
from jammate.orchestration.plans import make_plan
from jammate.orchestration.realtime import RealtimeSession, ScheduleSlot
from jammate.render.synth import render
from jammate.state import JamState


def session():
    return RealtimeSession(StateManager(JamState(session_id="live-test")), sample_rate=1000)


def observe(runtime, text, event_id="text"):
    return runtime.observe((from_text(text, session_id="live-test", event_id=event_id),))


def prepare(runtime, request, plan=None):
    return runtime.bind(request, plan or request.decide(RuleAgent())).prepare()


def scoped(notes, layer):
    return tuple(n for n in notes if n.instrument == layer)


def drum_count(bar):
    return len(scoped(bar.notes, "drums"))


def test_timing_policy_uses_playback_samples():
    runtime = session()
    first, = runtime.advance_to(0)
    assert first.timing.end_sample == 2500
    assert runtime.schedule_window() == (
        ScheduleSlot(1, 2500, 2300, 1950),
        ScheduleSlot(2, 5000, 4800, 4450),
        ScheduleSlot(3, 7500, 7300, 6950),
    )
    runtime.advance_to(2301)
    assert runtime.schedule_window()[0].bar == 2


def test_drums_change_at_planned_start_but_audio_before_it_is_preserved():
    runtime = session()
    first, = runtime.advance_to(0)
    request = observe(runtime, "鼓收一点")
    runtime.advance_to(1950)
    change = prepare(runtime, request)
    assert change.decision.slot.bar == 1
    runtime.advance_to(2300)
    assert runtime.schedule(change)
    assert runtime.manager.state.layer("drums").density == 1.0
    assert runtime.manager.plan_status(change.decision.plan.plan_id) == "pending"
    assert runtime.advance_to(2499) == ()
    second, = runtime.advance_to(2500)
    assert second.started.plan_id == change.decision.plan.plan_id
    assert second.started.start_sample == first.timing.end_sample == 2500
    assert runtime.manager.state.layer("drums").density < 1.0
    assert runtime.manager.state.position.bar == 1
    assert runtime.manager.state.position.next_bar == 2
    assert runtime.manager.state.section_bars == 0  # New section has just started.
    unchanged = prepare_bar(request.state, 1)
    assert drum_count(second) < len(scoped(unchanged, "drums"))
    for layer in ("bass", "keys"):
        assert scoped(second.notes, layer) == scoped(unchanged, layer)
    # The two consecutive note blocks are renderable without inserting samples.
    waves = [render(tuple(n.shifted(-bar.timing.bar_index * 4) for n in bar.notes),
                    bar.timing.tempo_bpm, runtime.sample_rate,
                    total_s=(bar.timing.end_sample-bar.timing.start_sample) / runtime.sample_rate)
             for bar in (first, second)]
    assert all(np.any(wave) for wave in waves)
    assert np.concatenate(waves).size == second.timing.end_sample
    after, = runtime.advance_to(5000)
    assert after.started is None and runtime.manager.state.section_bars == 1


def test_late_model_result_binds_to_the_next_feasible_bar():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "鼓收一点")
    runtime.advance_to(1951)
    change = prepare(runtime, request)
    assert change.decision.slot.bar == 2
    assert runtime.schedule(change)
    unchanged, = runtime.advance_to(2500)
    changed, = runtime.advance_to(5000)
    assert unchanged.started is None
    assert changed.started.start_sample == unchanged.timing.end_sample
    assert drum_count(changed) < drum_count(unchanged)


def test_late_preparation_cannot_enqueue_the_expired_bar():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "鼓收一点")
    plan = request.decide(RuleAgent())
    change = prepare(runtime, request, plan)
    runtime.advance_to(2301)
    with pytest.raises(PlanError, match="enqueue deadline missed"):
        runtime.schedule(change)
    assert runtime.manager.plan_status(plan.plan_id) is None
    rebound = prepare(runtime, request, plan)
    assert rebound.decision.slot.bar == 2 and runtime.schedule(rebound)


def test_request_cannot_chase_the_rolling_window_after_its_deadline():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "鼓收一点")
    assert request.decision_deadline_sample == 6950
    bars = runtime.advance_to(6951)
    with pytest.raises(PlanError, match="decision deadline missed"):
        prepare(runtime, request)
    assert all(bar.started is None and bar.notes for bar in bars)
    assert runtime.manager.state.layer("drums").density == 1.0


def test_new_input_cancels_unstarted_plan_and_rejects_old_worker_result():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "鼓收一点", "old")
    plan = request.decide(RuleAgent())
    old = prepare(runtime, request, plan)
    runtime.schedule(old)
    runtime.advance_to(2399)  # Enqueue has closed, playback has not begun.
    followup = observe(runtime, "慢一点", "new")
    assert runtime.manager.plan_status(plan.plan_id) == "cancelled"
    with pytest.raises(PlanError, match="stale"):
        runtime.bind(request, plan)
    with pytest.raises(PlanError, match="stale"):
        runtime.schedule(old)
    new = prepare(runtime, followup)
    assert runtime.schedule(new)
    first, second = runtime.advance_to(5000)
    assert first.started is None and first.timing.tempo_bpm == 96
    assert second.started.plan_id == new.decision.plan.plan_id and second.timing.tempo_bpm == 88
    assert runtime.manager.state.layer("drums").density == 1.0


def test_duplicate_event_schedule_and_playback_receipt_are_idempotent():
    runtime = session()
    runtime.advance_to(0)
    event = from_text("鼓收一点", session_id="live-test", event_id="once")
    change = prepare(runtime, runtime.observe((event,)))
    assert runtime.schedule(change)
    assert not runtime.schedule(change)
    assert runtime.observe((event,)) is None
    bar, = runtime.advance_to(2500)
    version = runtime.manager.state.state_version
    assert not runtime.manager.playback_started(change.decision.plan, bar=bar.started.bar,
                                                start_sample=bar.started.start_sample)
    assert runtime.manager.state.state_version == version
    with pytest.raises(PlanError, match="boundary"):
        runtime.manager.playback_started(change.decision.plan, bar=2, start_sample=5000)


def test_playback_continues_while_llm_worker_is_blocked():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "鼓太满了，留点空间")
    entered, release = Event(), Event()

    class DelayedProvider:
        def complete(self, messages, schema, timeout):
            entered.set()
            assert release.wait(8)
            decision = {
                "strategy": "LISTEN", "valid_for_bars": 4,
                "preserve": ["tempo", "key", "layer:bass", "layer:keys"],
                "actions": [{"action": "SIMPLIFY", "scope": "drums", "strength": 0.5,
                             "parameters": {"duration_bars": None}}],
                "transport": {"playing": None, "tempo_bpm": None},
                "reason": "鼓点留出空间", "unresolved": [],
            }
            return ModelReply(json.dumps(decision), "scripted", {})

    agent = LLMAgent(LLMConfig(api_key="scripted", timeout_s=15), provider=DelayedProvider(), fallback="error")
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(request.decide, agent)
        try:
            assert entered.wait(8)
            live, = runtime.advance_to(2500)
            assert not result.done() and live.started is None and live.notes
        finally:
            release.set()
        plan = result.result(timeout=8)
    bound = runtime.bind(request, plan)
    with ThreadPoolExecutor(max_workers=1) as worker:
        change = worker.submit(bound.prepare).result(timeout=8)
    assert runtime.schedule(change)
    changed, = runtime.advance_to(5000)
    assert changed.started.bar == 2 and drum_count(changed) < drum_count(live)


def test_stop_invalidation_and_resume_keep_one_transport():
    runtime = session()
    runtime.advance_to(0)
    old = prepare(runtime, observe(runtime, "鼓收一点"))
    runtime.schedule(old)
    stop = runtime.observe((from_system("stop", session_id="live-test", event_id="stop"),))
    assert runtime.schedule(prepare(runtime, stop))
    silent, = runtime.advance_to(2500)
    assert silent.started is not None and not silent.notes
    assert not runtime.manager.state.playing
    assert runtime.manager.state.layer("drums").density == 1.0
    start = observe(runtime, "继续", "start")
    assert runtime.schedule(prepare(runtime, start))
    resumed, = runtime.advance_to(5000)
    assert resumed.started.start_sample == silent.timing.end_sample
    assert resumed.notes and runtime.manager.state.playing


def test_tempo_changes_lengths_after_unchanged_boundary():
    runtime = session()
    first, = runtime.advance_to(0)
    request = observe(runtime, "速度设为 120 BPM")
    plan = make_plan(request.state, request.events, "tempo", "FOLLOW", [], ["key"],
                     {"tempo_bpm": 120}, 4, "new tempo", [])
    assert runtime.schedule(prepare(runtime, request, plan))
    changed, continued = runtime.advance_to(4500)
    assert changed.timing.start_sample == first.timing.end_sample == 2500
    assert changed.timing.end_sample == continued.timing.start_sample == 4500
    assert changed.timing.tempo_bpm == continued.timing.tempo_bpm == 120


def test_clarification_keeps_accompaniment_and_leaves_plan_unstaged():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "调一下那个")
    plan = make_plan(request.state, request.events, "ambiguous", "LISTEN", [], ["tempo"],
                     {}, 4, "请确认声部", ["哪个声部"])
    with pytest.raises(PlanError, match="clarification"):
        prepare(runtime, request, plan)
    played, = runtime.advance_to(2500)
    assert played.started is None and played.notes
    assert runtime.manager.plan_status(plan.plan_id) is None


def test_preservation_is_checked_against_an_active_prepared_ramp():
    runtime = session()
    runtime.advance_to(0)
    ramp = observe(runtime, "鼓更有力")
    assert runtime.schedule(prepare(runtime, ramp))
    first, = runtime.advance_to(2500)
    follow = observe(runtime, "键盘收一点", "followup")
    change = prepare(runtime, follow)
    with pytest.raises(PlanError, match="queued layer:drums"):
        runtime.schedule(change)
    second, = runtime.advance_to(5000)
    assert second.started is None
    assert scoped(second.notes, "drums")[0].velocity > scoped(first.notes, "drums")[0].velocity



def test_positive_feedback_keeps_an_active_ramp_playing():
    runtime = session()
    runtime.advance_to(0)
    ramp = prepare(runtime, observe(runtime, "鼓更有力"))
    assert runtime.schedule(ramp)
    runtime.advance_to(2500)
    request = observe(runtime, "这样很好，继续", "positive")
    plan = make_plan(request.state, request.events, "positive", "FOLLOW", [],
                     ["tempo", "key", "layer:drums"], {}, 4, "保持原样", [])
    assert runtime.schedule(prepare(runtime, request, plan))
    continued, = runtime.advance_to(5000)
    assert continued.started.plan_id == "positive"
    expected = tuple(note for note in ramp.performance.notes if 8 <= note.start < 12)
    assert continued.notes == expected
