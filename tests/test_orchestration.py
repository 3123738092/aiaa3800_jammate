"""Behavioral acceptance for the versioned offline Agent loop."""
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from jammate.contracts import BandPlan, InputEvent, PlanError, load_input_event
from jammate.contracts.adapters import from_text, from_system, from_humming
from jammate.contracts.state_codec import state_schema
from jammate.interaction import Motif, Strategy
from jammate.music import Note
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime, render_plan
from jammate.orchestration.transitions import project
from jammate.session_log import SessionLog
from jammate.state import JamState

ROOT = Path(__file__).resolve().parents[1]
SID = "jam_demo"


def text_event(text, event_id="text_1", **kwargs):
    return from_text(text, session_id=SID, event_id=event_id, **kwargs)


def system(command, event_id="system_1", **kwargs):
    return from_system(command, session_id=SID, event_id=event_id, **kwargs)


def manager(**kwargs):
    return StateManager(JamState(session_id=SID, tempo_bpm=100, **kwargs))


def staged(m, events, plan_id="p1"):
    accepted = m.ingest(events)
    p = RuleAgent().decide(accepted, m.state, plan_id=plan_id)
    m.stage(p)
    return p


def execute(m, p):
    return OfflineRuntime(sample_rate=8000).execute(m, p)


def changed(plan, **changes):
    return BandPlan.from_dict({**plan.to_dict(), **changes})


def receipt(m, p, **kwargs):
    d = p.to_dict()
    return m.acknowledge(p, effective_bar=d["effective_at"]["bar"],
                         completed_bar=d["effective_at"]["bar"] + d["valid_for_bars"], **kwargs)


def notes_for(result, instrument):
    return tuple(n for n in result.notes if n.instrument == instrument)


def test_published_state_schema_and_roundtrip():
    published = json.loads((ROOT / "src/jammate/contracts/jam_state.schema.json").read_text(encoding="utf-8"))
    assert published == state_schema()
    m = manager()
    m.ingest([load_input_event(ROOT / "examples/input_events/humming.json"), load_input_event(ROOT / "examples/input_events/rhythm.json")])
    state = m.state
    assert JamState.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    copy = state.to_dict()
    copy["arrangement"]["layers"][0]["level"] = 0
    assert state.layer("drums").level == .5
    assert state.rhythm is None and state.current_motif_id is None  # observation is not execution
    assert state.motifs and state.rhythm_sources


def test_simplify_sparse_keys_changes_actual_notes_across_bars():
    m = manager()
    p = staged(m, [text_event("键盘简化")])
    # Explicit action keeps this an executor test independent of text lexicons.
    p = changed(p, valid_for_bars=4, preserve=["tempo", "key"], actions=[{
        "action": "SIMPLIFY", "scope": "keys", "strength": .5, "parameters": {}}])
    baseline = render_plan(m.state, changed(p, actions=[]), sample_rate=8000)
    result = render_plan(m.state, p, sample_rate=8000)
    assert len(notes_for(result, "keys")) < len(notes_for(baseline, "keys"))
    assert notes_for(result, "bass") == notes_for(baseline, "bass")
    assert notes_for(result, "drums") == notes_for(baseline, "drums")
    # Chord tones stay together rather than being thinned individually.
    groups = {}
    for note in notes_for(result, "keys"):
        groups.setdefault(note.start, []).append(note)
    assert groups and all(len(group) == 3 for group in groups.values())


@pytest.mark.parametrize("path,value", [
    (("transport", "tempo_bpm"), float("nan")), (("transport", "next_bar"), -1),
    (("feel", "density"), 2), (("interaction", "strategy"), "STOP"),
    (("motifs", "current_id"), "missing"), (("state_version",), -1),
])
def test_invalid_state_rejected(path, value):
    d = manager().state.to_dict()
    target = d
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        JamState.from_dict(d)


def test_lifecycle_is_additive_11_contract():
    event = system("idle", bars=2, occurred_at=12.)
    assert event.schema_version == "1.1.0" and event.started_at == event.ended_at == 12.
    assert InputEvent.from_dict(event.to_dict()) == event
    with pytest.raises(ValueError):
        InputEvent.from_dict({**event.to_dict(), "schema_version": "1.0.0"})
    with pytest.raises(ValueError):
        system("user_activity")
    with pytest.raises(ValueError):
        system("idle", bars=-1)
    with pytest.raises(ValueError):
        system("stop", active=True)
    with pytest.raises(ValueError):
        system("start", bars=3)
    with pytest.raises(ValueError):
        InputEvent.from_dict({**event.to_dict(), "ended_at": 13.})


@pytest.mark.parametrize("words,action,scope", [
    ("这个不错，鼓再猛一点", "BUILD", "drums"), ("再炸一点", "BUILD", "band"),
    ("太满了，留点空间", "SIMPLIFY", "band"), ("鼓收一点", "SIMPLIFY", "drums"),
    ("加贝斯", "ADD_LAYER", "bass"), ("去掉贝斯", "REMOVE_LAYER", "bass"),
    ("drums more powerful", "BUILD", "drums"), ("remove bass", "REMOVE_LAYER", "bass"),
    ("鼓停止", "REMOVE_LAYER", "drums"), ("贝斯继续", "ADD_LAYER", "bass"),
])
def test_supported_complete_clauses(words, action, scope):
    p = staged(manager(), [text_event(words)])
    matching = [a for a in p.actions if a["action"] == action]
    assert len(matching) == 1 and matching[0]["scope"] == scope
    assert not p.to_dict()["unresolved"]
    assert "playing" not in p.to_dict()["transport"]


@pytest.mark.parametrize("words", ["不要停", "别停", "don't stop", "不要加速", "别变速度", "鼓不要更猛"])
def test_negation_never_becomes_opposite_command(words):
    m = manager()
    p = staged(m, [text_event(words)])
    result = project(m.state, p)
    assert result.playing and result.tempo_bpm == 100
    assert result.layers == m.state.layers
    assert not p.to_dict()["unresolved"]


@pytest.mark.parametrize("words", ["把它弄得像蓝色的雨", "吉他更猛", "鼓更猛，鼓收一点", "加速，速度不变", "停止播放之后再继续", "鼓快点", "贝斯变奏"])
def test_uncertain_or_conflicting_request_holds(words):
    p = staged(manager(), [text_event(words)])
    assert not p.actions and p.to_dict()["strategy"] == "LISTEN"
    assert p.to_dict()["unresolved"]
    assert not p.to_dict()["transport"]


def test_scope_is_audible_and_build_ramps_without_changing_other_parts():
    m = manager()
    p = staged(m, [text_event("这个不错，鼓再猛一点")])
    before = m.state
    baseline = render_plan(before, changed(p, plan_id="baseline", actions=[]), sample_rate=8000)
    result = execute(m, p)
    assert m.state.state_version == 2 and before.state_version == 1
    for name in ("bass", "keys", "lead"):
        assert notes_for(result, name) == notes_for(baseline, name)
    old, new = notes_for(baseline, "drums"), notes_for(result, "drums")
    assert [(n.pitch, n.start, n.duration) for n in old] == [(n.pitch, n.start, n.duration) for n in new]
    assert all(a.velocity >= b.velocity for a, b in zip(new, old))
    assert any(a.velocity > b.velocity for a, b in zip(new, old))
    assert m.state.layer("drums").level == .75
    assert result.audio.size and np.isfinite(result.audio).all()


def test_simplify_reduces_real_notes_and_preserves_a_registered_motif():
    motif = Motif("m0", "user", ("old",), (Note(60, .5, .5), Note(64, 1.5, 1.)))
    m = manager(motifs=(motif,), current_motif_id="m0")
    p = staged(m, [text_event("太满了，留点空间")])
    before = m.state
    baseline = render_plan(before, changed(p, plan_id="baseline", actions=[]), sample_rate=8000)
    result = execute(m, p)
    assert len(result.notes) < len(baseline.notes)
    assert m.state.motif("m0") == motif and m.state.current_motif_id == "m0"
    assert m.state.tempo_bpm == 100 and m.state.key == before.key


def test_humming_echo_preserves_leading_rest_overlap_and_uses_session_tempo():
    m = manager()
    event = load_input_event(ROOT / "examples/input_events/humming.json")
    p = staged(m, [event])
    observed = m.state.motifs[0]
    assert observed.notes[0].start > 0 and m.state.current_motif_id is None
    result = execute(m, p)
    expected = tuple(replace(n, instrument="lead") for n in observed.notes)
    assert result.lead_notes == expected
    assert notes_for(result, "lead") == tuple(sorted(expected, key=lambda n: (n.start, n.instrument, n.pitch)))
    assert observed.notes[0].start == pytest.approx(event.payload["melody"]["notes"][0]["startSec"] * 100 / 60)
    assert m.state.current_motif_id == observed.motif_id
    assert m.state.interaction.strategy is Strategy.LISTEN


def test_explicit_multimodal_batch_has_one_version_and_one_plan():
    m = manager()
    events = [load_input_event(ROOT / f"examples/input_events/{name}.json") for name in ("rhythm", "humming")]
    p = staged(m, events)
    assert m.state.state_version == 1
    assert p.to_dict()["trigger_event_ids"] == [e.event_id for e in events]
    assert {a["action"] for a in p.actions} == {"ADJUST_FEEL", "ECHO_MOTIF"}
    execute(m, p)
    assert m.state.rhythm.onset_beats and m.state.current_motif_id and m.state.tempo_bpm == 100


def test_feedback_keeps_phrase_and_avoids_legacy_stop_bug():
    m = manager()
    p = staged(m, [load_input_event(ROOT / "examples/input_events/humming.json")])
    execute(m, p)
    motif = m.state.motif(m.state.current_motif_id)
    p2 = staged(m, [text_event("别停，鼓收一点，刚才那句保留。")], "p2")
    execute(m, p2)
    assert m.state.playing and m.state.motif(motif.motif_id) == motif
    assert m.state.layer("drums").density < 1 and m.state.layer("bass").density == 1


def test_idle_initiates_once_then_waits_and_activity_prevents_interrupting():
    m = manager()
    p1 = staged(m, [system("idle", bars=2)])
    assert p1.to_dict()["strategy"] == "INITIATE"
    result = execute(m, p1)
    assert result.generated_motif is not None and m.state.interaction.awaiting_user
    assert m.state.interaction.strategy is Strategy.LISTEN
    p2 = staged(m, [system("idle", "s2", bars=2)], "p2")
    assert not p2.actions
    execute(m, p2)
    assert len(m.state.motifs) == 1
    p3 = staged(m, [system("user_activity", "s3", active=True), system("idle", "s4", bars=5)], "p3")
    assert p3.to_dict()["strategy"] == "LISTEN"
    assert not any(a["action"].endswith("MOTIF") for a in p3.actions)


def test_stop_wins_cancels_pending_and_requires_explicit_restart():
    m = manager()
    old = staged(m, [text_event("更猛")])
    stop = staged(m, [text_event("停止，更猛", "t2")], "stop")
    assert m.plan_status(old.plan_id) == "cancelled"
    assert not execute(m, stop).notes and not m.state.playing
    p = staged(m, [system("idle", bars=5)], "idle")
    assert not p.actions
    execute(m, p)
    start = staged(m, [text_event("继续，鼓更猛", "t3")], "start")
    assert execute(m, start).notes and m.state.playing
    assert m.state.layer("drums").level > .5 and m.state.layer("bass").level == .5


def test_duplicate_event_conflict_and_wrong_session_are_atomic():
    m = manager()
    event = text_event("更猛")
    p = staged(m, [event])
    assert not m.ingest([event, event]) and m.state.state_version == 1
    for invalid in [text_event("简化"), from_text("保持", session_id="another")]:
        with pytest.raises(ValueError):
            m.ingest([text_event("简化", "new"), invalid])
        assert m.state.state_version == 1 and m.plan_status(p.plan_id) == "pending"
    assert len(m.ingest([text_event("简化", "new")])) == 1


def test_stale_recognition_is_recorded_but_does_not_undo_new_input():
    m = manager()
    fresh = text_event("保持", started_at=20., ended_at=21.)
    p = staged(m, [fresh])
    assert not m.ingest([text_event("停止", "late", started_at=10., ended_at=11.)])
    assert m.state.state_version == 1 and m.plan_status(p.plan_id) == "pending"


def test_stale_plan_and_late_receipt_rejected_after_new_input():
    m = manager()
    p = staged(m, [text_event("更猛")])
    m.ingest([text_event("简化", "t2")])
    with pytest.raises(PlanError):
        receipt(m, p, succeeded=True)
    assert m.state.layer("drums").level == .5
    with pytest.raises(PlanError):
        m.stage(changed(p, plan_id="replacement", trigger_event_ids=["t2"]))


def test_failed_and_duplicate_receipts_never_apply_twice():
    m = manager()
    p = staged(m, [text_event("更猛")])
    before = m.state
    assert receipt(m, p, succeeded=False, reason="backend unavailable")
    assert m.state == before and m.plan_status(p.plan_id) == "failed"
    assert not receipt(m, p, succeeded=True)
    p2 = staged(m, [text_event("更猛", "t2")], "p2")
    execute(m, p2)
    current = m.state
    assert not receipt(m, p2, succeeded=True)
    assert m.state == current
    with pytest.raises(PlanError):
        m.stage(changed(p2, plan_id="replay", based_on_state_version=current.state_version,
                        effective_at={"bar": current.position.next_bar, "beat": 0.}))


def test_runtime_ticks_do_not_invalidate_version_but_expire_missed_boundary():
    m = manager()
    p = staged(m, [text_event("更猛")])
    m.update_transport(bar=0, beat=.25, next_bar=1)
    assert m.state.state_version == 1 and m.plan_status(p.plan_id) == "expired"
    with pytest.raises(PlanError):
        execute(m, p)
    with pytest.raises(ValueError):
        m.update_transport(bar=0, beat=0, next_bar=0)


def test_plan_cannot_change_preserved_state_or_inject_backend_notes():
    m = manager()
    p = staged(m, [text_event("保持")])
    with pytest.raises(PlanError):
        project(m.state, changed(p, transport={"tempo_bpm": 110}))
    d = p.to_dict()
    d["actions"][0]["parameters"]["notes"] = [{"pitch": 60}]
    with pytest.raises(PlanError):
        BandPlan.from_dict(d)


def test_preserved_groove_checks_notes_and_failed_render_does_not_commit():
    m = manager()
    p = staged(m, [text_event("节奏保持，鼓收一点")])
    before = m.state
    with pytest.raises(PlanError, match="preserved groove"):
        execute(m, p)
    assert m.state == before and m.plan_status(p.plan_id) == "failed"


def test_unknown_key_is_preserved_and_renderer_reports_capability_failure():
    m = manager(key=None)
    p = staged(m, [text_event("保持")])
    with pytest.raises(PlanError, match="session key"):
        execute(m, p)
    assert m.state.key is None and m.state.state_version == 1


def test_trace_connects_observation_plan_receipt_and_final_state(tmp_path):
    path = tmp_path / "trace.jsonl"
    m = StateManager(JamState(session_id=SID), log=SessionLog(path))
    p = staged(m, [text_event("鼓更猛")])
    execute(m, p)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["type"] for r in records] == ["state_initialized", "input_event", "state_observed", "band_plan", "execution_receipt", "state_applied"]
    assert records[-1]["state"] == m.state.to_dict()
    assert records[3]["plan"]["trigger_event_ids"] == [records[1]["event"]["event_id"]]
