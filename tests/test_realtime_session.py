from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event

import numpy as np
import pytest

from jammate.contracts import PlanError
from jammate.contracts.adapters import from_system, from_text
from jammate.generate.arranger import chord_for_bar
from jammate.generate.harmony import harmony_cycle
from jammate.interaction import Layer
from jammate.music import Key, Note
from jammate.orchestration.addition_intent import guitar_command_plan
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_bar
from jammate.orchestration.plans import make_plan
from jammate.orchestration.realtime import RealtimeSession, ScheduleSlot
from jammate.render.synth import render
from jammate.rhythm import RhythmContext
from jammate.state import JamState
from jammate.texture import ArrangementTexture


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


@pytest.mark.parametrize("agent", [RuleAgent(), LLMAgent(LLMConfig("test"), provider=object())])
def test_explicit_key_change_is_applied_on_the_scheduled_downbeat(agent):
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "换一个调", "unclear")
    plan = request.decide(agent)
    assert "想换到哪个调" in plan.to_dict()["unresolved"][0]
    assert runtime.manager.hold(plan)
    assert runtime.manager.state.key == Key(0)

    request = observe(runtime, "换成D大调", "named")
    change = prepare(runtime, request, request.decide(agent))
    assert runtime.schedule(change)
    assert runtime.manager.state.key == Key(0)
    assert runtime.advance_to(2499) == ()
    changed, = runtime.advance_to(2500)
    assert changed.started.plan_id == change.decision.plan.plan_id
    assert runtime.manager.state.key == Key(2)
    assert changed.timing.tempo_bpm == request.state.tempo_bpm
    baseline = prepare_bar(request.state, 1)
    assert scoped(changed.notes, "drums") == scoped(baseline, "drums")
    assert scoped(changed.notes, "keys") != scoped(baseline, "keys")
    assert changed.notes == prepare_bar(request.state.with_updates(key=Key(2)), 1)


def test_repeat_request_for_more_instruments_reports_the_existing_lineup():
    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "再加一些乐器", "first-add")
    change = prepare(runtime, request)
    assert runtime.schedule(change)
    joined, = runtime.advance_to(2500)
    assert scoped(joined.notes, "guitar")

    request = observe(runtime, "再加一些乐器", "second-add")
    plan = request.decide(RuleAgent())
    assert not plan.actions
    assert "木吉他已经加入" in plan.to_dict()["unresolved"][0]
    assert runtime.manager.hold(plan)
    assert sum(layer.name == "guitar" for layer in runtime.manager.state.layers) == 1


def test_authored_bass_and_guitar_continue_and_level_edits_keep_the_riff():
    initial = JamState(session_id="live-test", auto_harmony=False,
                       layers=(Layer("drums"), Layer("bass", active=False), Layer("keys"), Layer("lead")))
    runtime = RealtimeSession(StateManager(initial), sample_rate=1000)
    runtime.advance_to(0)
    request = observe(runtime, "加一条弹性的贝斯线", "bassline")
    plan = make_plan(request.state, request.events, "bassline", "FOLLOW",
                     [{"action": "ADD_LAYER", "scope": "bass", "strength": .5, "parameters": {}}],
                     ["tempo", "key", "progression", "playing", "groove"], {}, 4, "加贝斯", [])
    bass = prepare(runtime, request, plan)
    groove = tuple(Note(38, 4.*bar + 1.25, .5, 85, "bass")
                   for bar in range(bass.performance.start_bar, bass.performance.start_bar + bass.performance.bars))
    backing = replace(bass.performance,
                      notes=tuple(n for n in bass.performance.notes if n.instrument != "bass") + groove)
    assert runtime.schedule(replace(bass, performance=backing), repeat_layers=("bass",))
    first, = runtime.advance_to(bass.decision.slot.start_sample)
    runtime.advance_to(first.timing.start_sample + 4 * (first.timing.end_sample - first.timing.start_sample))

    request = observe(runtime, "来个清楚的电吉他 riff", "riff")
    plan, _ = guitar_command_plan(request.events, request.state, "riff", {27: "Clean"})
    change = prepare(runtime, request, plan)
    source = change.performance
    riff = tuple(Note(69 + offset, 4.0 * bar + offset / 2., .5, 60, "gm_27")
                 for bar in range(source.start_bar, source.start_bar + source.bars) for offset in range(2))
    perf = replace(source, notes=tuple(n for n in source.notes if n.instrument != "gm_27") + riff)
    assert runtime.schedule(replace(change, performance=perf), repeat_layers=("gm_27",))
    at = change.decision.slot.start_sample
    joined, = runtime.advance_to(at)
    step = joined.timing.end_sample - at
    runtime.advance_to(at + (source.bars - 1)*step)
    looped, = runtime.advance_to(at + source.bars*step)
    assert looped.plan is None
    assert scoped(looped.notes, "gm_27") == tuple(n.shifted(4*source.bars) for n in riff[:2])

    request = observe(runtime, "吉他再突出一点，bass 不要变，鼓也保持", "louder")
    plan, _ = guitar_command_plan(request.events, request.state, "louder", {27: "Clean"})
    louder = prepare(runtime, request, plan)
    point = louder.decision.slot
    assert runtime.schedule(louder, reuse_local_score=True)
    predicted = runtime.preview_next_bar()
    raised, = runtime.advance_to(point.start_sample)
    assert predicted.notes == raised.notes
    assert raised.started.plan_id == plan.plan_id
    assert scoped(raised.notes, "gm_27") == tuple(
        replace(n.shifted(4*(point.bar - source.start_bar)), velocity=84) for n in riff[:2]
    )
    assert scoped(raised.notes, "bass") == (groove[0].shifted(4 * (point.bar - bass.performance.start_bar)),)
    assert scoped(raised.notes, "drums") == scoped(prepare_bar(request.state, point.bar), "drums")

    request = observe(runtime, "去掉吉他，保留鼓和 bass", "mute")
    plan, _ = guitar_command_plan(request.events, request.state, "mute", {27: "Clean"})
    mute = prepare(runtime, request, plan)
    assert runtime.schedule(mute, reuse_local_score=True)
    stopped, = runtime.advance_to(mute.decision.slot.start_sample)
    assert not scoped(stopped.notes, "gm_27")
    assert scoped(stopped.notes, "bass") == (groove[0].shifted(4 * (stopped.timing.bar_index - bass.performance.start_bar)),)
    assert not scoped(runtime.preview_next_bar().notes, "gm_27")


def test_llm_arrangement_changes_multiple_parts_and_persists_past_the_plan():
    class TextureDirector:
        def complete(self, messages, schema, timeout_s):
            action = {"action": "SET_TEXTURE", "scope": "band", "strength": 1,
                      "parameters": {"scale": "pentatonic", "drums": "sparse", "bass": "drone", "keys": "arpeggio"}}
            return ModelReply(json.dumps({"strategy": "FOLLOW", "actions": [action], "valid_for_bars": 1,
                                          "preserve": ["tempo", "key", "playing"],
                                          "transport": {"tempo_bpm": None, "playing": None},
                                          "reason": "改变伴奏织体", "unresolved": []}), "test-model", {})

    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "能不能全部换成民乐风格")
    agent = LLMAgent(LLMConfig("test"), provider=TextureDirector(), fallback="error")
    plan = request.decide(agent)
    assert agent.last_trace["source"] == "llm"
    change = prepare(runtime, request, plan)
    assert runtime.schedule(change)
    assert runtime.manager.state.texture == ArrangementTexture()
    arranged, = runtime.advance_to(2500)
    assert arranged.started.plan_id == plan.plan_id
    assert runtime.manager.state.key == request.state.key
    assert runtime.manager.state.texture == ArrangementTexture("pentatonic", "sparse", "drone", "arpeggio")
    continued, = runtime.advance_to(5000)
    assert continued.started is None
    for bar in (arranged, continued):
        original = prepare_bar(request.state, bar.timing.bar_index)
        for layer in ("drums", "keys", "bass"):
            assert scoped(original, layer) != scoped(bar.notes, layer)
        assert all(note.pitch % 12 in {0, 2, 4, 7, 9} for note in bar.notes if note.instrument != "drums")


def test_model_replaces_the_chord_cycle_and_starts_it_at_the_actual_playback_bar():
    class ProgressionDirector:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, schema, timeout_s):
            self.calls += 1
            # The first proposal just rotates C–G–Am–F. The second actually changes the harmony.
            proposed = [(7, "maj"), (9, "min"), (5, "maj"), (0, "maj")] if self.calls == 1 else [
                (2, "min7"), (7, "dom7"), (0, "maj7"),
            ]
            action = {"action": "SET_PROGRESSION", "scope": "band", "strength": 1,
                      "parameters": {"chords": [{"offset": root, "quality": quality} for root, quality in proposed]}}
            return ModelReply(json.dumps({"strategy": "FOLLOW", "actions": [action], "valid_for_bars": 3,
                                          "preserve": ["tempo", "key", "playing", "groove"],
                                          "transport": {"tempo_bpm": None, "playing": None},
                                          "reason": "切换和弦", "unresolved": []}), "test-model", {})

    runtime = session()
    runtime.advance_to(0)
    request = observe(runtime, "能不能换一个和弦进行")
    provider = ProgressionDirector()
    agent = LLMAgent(LLMConfig("test"), provider=provider, fallback="error")
    plan = request.decide(agent)
    assert provider.calls == 2
    assert "rotation" in agent.last_trace["attempts"][0]["validation_error"]
    runtime.advance_to(1951)
    change = prepare(runtime, request, plan)
    assert change.decision.slot.bar == 2
    assert runtime.schedule(change)
    unchanged, = runtime.advance_to(2500)
    assert unchanged.started is None and not runtime.manager.state.chord_cycle
    switched, = runtime.advance_to(5000)
    state = runtime.manager.state
    assert switched.started.plan_id == plan.plan_id
    assert state.key == request.state.key and state.tempo_bpm == request.state.tempo_bpm
    assert state.progression_start_bar == 2
    assert [chord.name for chord in harmony_cycle(state)] == ["Dm7", "G7", "Cmaj7"]
    assert {note.pitch % 12 for note in scoped(switched.notes, "keys")} == {0, 2, 5, 9}
    assert scoped(switched.notes, "drums") == scoped(prepare_bar(request.state, 2), "drums")
    assert chord_for_bar(state, 3).name == "G7" and chord_for_bar(state, 5).name == "Dm7"


def test_named_seventh_chords_use_the_existing_recorded_rhythm():
    rhythm = RhythmContext(96, (0., 1.5, 3.), (1., .7, .8))
    state = JamState(session_id="live-test", rhythm=rhythm, texture=ArrangementTexture("pentatonic", keys="open"))
    runtime = RealtimeSession(StateManager(state), sample_rate=1000)
    runtime.advance_to(0)
    request = observe(runtime, "把和弦进行改成 Dm7–G7–Cmaj7")
    assert runtime.schedule(prepare(runtime, request))
    changed, = runtime.advance_to(2500)
    assert runtime.manager.state.rhythm == rhythm
    assert {note.pitch % 12 for note in scoped(changed.notes, "keys")} == {0, 2, 5, 9}


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
