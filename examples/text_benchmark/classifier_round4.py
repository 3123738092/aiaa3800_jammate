"""Frozen multi-turn checks through the web worker and shared decision gates."""
import argparse
import base64
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pretty_midi

from classifier_round2 import state_summary
from jammate.contracts import BandPlan, PlanError
from jammate.contracts.adapters import from_text
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.explicit_request import compile_jev_request
from jammate.orchestration.llm import LLMAgent, ModelReply, OpenAIProvider, ProviderFailure, event_context
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.orchestration.plans import make_plan
from jammate.state import JamState
from scripts import reference_worker


def proposal(*, tempo=None, action=None, unresolved=None):
    return {"strategy": "FOLLOW", "actions": [action] if action else [],
            "preserve": ["key", "playing"],
            "transport": {"tempo_bpm": tempo, "playing": None},
            "valid_for_bars": 4, "reason": "round 4 scripted provider reply",
            "unresolved": [unresolved] if unresolved else []}


def scripted_reply(event_id):
    if event_id == "question":
        return proposal(unresolved="你要暂停鼓还是键盘？")
    if event_id == "answer":
        return proposal(action={"action": "REMOVE_LAYER", "scope": "keys", "strength": 0.5, "parameters": {}})
    if event_id == "partial":
        return proposal(tempo=96, unresolved="当前不能把和声调式改成 C 小调。")
    return proposal(tempo={"seed": 102, "relative": 98}[event_id])


class RecordingProvider:
    def __init__(self, delegate=None):
        self.delegate = delegate
        self.calls = []

    def complete(self, messages, schema, timeout_s):
        context = json.loads(messages[1]["content"])
        event_id = context["events"][0]["event_id"]
        evidence = {"event_id": event_id, "context": context}
        self.calls.append(evidence)
        start = perf_counter()
        try:
            if self.delegate is None:
                reply = ModelReply(json.dumps(scripted_reply(event_id), ensure_ascii=False), "scripted", {})
            else:
                reply = self.delegate.complete(messages, schema, timeout_s)
        except ProviderFailure as exc:
            evidence["failure"] = exc.kind
            raise
        finally:
            evidence["elapsed_ms"] = round((perf_counter() - start) * 1000, 2)
        evidence["model"] = reply.model
        return reply


def music(state):
    return (state["bpm"], state["next_bar"], state["playing"], state["layers"])


def held_history(history):
    return next((turn for turn in history
                 if any(item["event_id"] == "question" for item in turn["events"])
                 and any(entry["status"] == "held" and entry["plan"]["unresolved"]
                         for entry in turn["plans"])), None)


def rendered_info(response):
    midi = base64.b64decode(response["midi"])
    wav = base64.b64decode(response["wav"])
    instruments = pretty_midi.PrettyMIDI(BytesIO(midi)).instruments
    return {"midi_sha256": sha256(midi).hexdigest(), "wav_sha256": sha256(wav).hexdigest(),
            "wav_bytes": len(wav), "note_counts": dict(Counter({
                part.name: len(part.notes) for part in instruments
            })), "plan": response["plan"], "start_bar": response["start_bar"],
            "bars": response["bars"], "turn": response["turn"]}


def run_worker(fixture, mode, config, provider):
    worker = reference_worker.ReferenceService(None, None)
    sid = f"round4-{mode}"
    prior_rows, texts = {}, {}
    state = JamState(session_id=sid, tempo_bpm=fixture["start_bpm"])
    with patch.object(worker, "config", return_value=config), patch.object(
            reference_worker, "LLMAgent", side_effect=lambda c, **kw: LLMAgent(c, provider=provider, **kw)):
        for case in fixture[mode]:
            case_id = case["id"]
            current_text = case["text"] if "text" in case else texts[case["same_as"]]
            texts[case_id] = current_text
            previous_calls = len(provider.calls)
            before = state_summary(state)
            start = perf_counter()
            response = None
            error = None
            try:
                response = worker.text_turn({
                    "session_id": sid, "agent": "llm", "profile": "electric",
                    "bpm": fixture["start_bpm"], "text": current_text,
                    "request_id": case["request"], "after_turn": case["after_turn"],
                })
            except ValueError as exc:
                error = str(exc)
                outcome = "held" if error.startswith("这条要求无法执行") else "rejected"
            except ProviderFailure as exc:
                error = exc.kind
                outcome = "provider-error"
            else:
                outcome = "cached" if case.get("same_as") and len(provider.calls) == previous_calls else "applied"
            response_info = rendered_info(response) if response is not None else None
            manager = worker.works[sid].manager
            state, history = manager.decision_snapshot()
            after = state_summary(state)
            evidence = provider.calls[previous_calls:]
            checks = {
                "outcome": outcome == case["outcome"], "tempo": after["bpm"] == case["tempo"],
                "next_bar": after["next_bar"] == case["bar"], "still_playing": after["playing"],
                "keys": after["layers"]["keys"]["active"] == case["keys"],
                "drums": after["layers"]["drums"]["active"] == case["drums"],
            }
            if "calls" in case:
                checks["provider_calls_total"] = len(provider.calls) == case["calls"]
            if outcome == "held":
                hold = held_history(history)
                checks["music_unchanged"] = music(before) == music(after)
                checks["version_advances_only_on_intake"] = (
                    after["state_version"] == before["state_version"] + int(not case.get("same_as"))
                )
                checks["held_visible"] = hold is not None and hold["state_after"] is None
                if case.get("same_as"):
                    checks["same_held_message"] = error == prior_rows[case["same_as"]]["error"]
            if case.get("requires_held_context"):
                context = evidence[0]["context"] if evidence else {}
                hold = held_history(context.get("recent_turns", []))
                archived = prior_rows["ambiguity"]["held_history"]
                expected_context = ({**archived, "events": [event_context(item) for item in archived["events"]]}
                                    if archived is not None else None)
                checks["exact_held_plan_visible_to_provider"] = (
                    hold is not None and hold == expected_context
                )
                checks["clarification_is_only_current_event"] = [
                    item["event_id"] for item in context.get("events", [])
                ] == ["answer"]
            if outcome in {"rejected", "cached"}:
                checks["no_new_state_or_provider_call"] = before == after and not evidence
            if outcome == "cached":
                checks["same_artifacts"] = response_info == prior_rows[case["same_as"]]["response"]
            if outcome == "applied":
                checks["rendered_and_applied"] = bool(response_info and response_info["wav_bytes"]
                    and manager.plan_status(response_info["plan"]["plan_id"]) == "applied")
                checks["unresolved"] = bool(response_info["plan"]["unresolved"]) == case.get("unresolved", False)
                if case_id == "clarification":
                    checks["keys_absent_in_midi"] = response_info["note_counts"].get("keys", 0) == 0
            row = {
                "case": case_id, "mode": mode, "outcome": outcome, "error": error,
                "before": before, "after": after, "provider_total": len(provider.calls), "provider_calls": evidence,
                "response": response_info, "held_history": held_history(history),
                "checks": checks, "passed": all(checks.values()),
                "elapsed_ms": round((perf_counter() - start) * 1000, 2),
            }
            prior_rows[case_id] = row
    return list(prior_rows.values()), manager


def reject(call):
    try:
        call()
    except PlanError as exc:
        return str(exc)
    return None


def run_direct(fixture, manager):
    rows = []
    for case in fixture["direct"]:
        case_id = case["id"]
        if case_id == "held_cannot_execute":
            state, history = manager.decision_snapshot()
            entry = held_history(history)["plans"][0]
            plan = BandPlan.from_dict(entry["plan"])
            start_bar = plan.to_dict()["effective_at"]["bar"]
            errors = [reject(lambda: OfflineRuntime().execute(manager, plan)),
                      reject(lambda: manager.acknowledge(plan, succeeded=True, effective_bar=start_bar,
                                                         completed_bar=start_bar + 4))]
            checks = {"execute_and_ack_reject": all(errors), "state_immutable": manager.state == state,
                      "status_still_held": manager.plan_status(plan.plan_id) == "held"}
        elif case_id == "preempted_plan":
            stale = StateManager(JamState(session_id="preempted"))
            old = stale.ingest([from_text("速度设到 110 BPM。", session_id="preempted", event_id="old")])
            original = stale.state
            old_plan = make_plan(original, old, "old", "FOLLOW", [], [], {"tempo_bpm": 110}, 4, "old", [])
            duplicate = make_plan(original, old, "other", "FOLLOW", [], [], {"tempo_bpm": 110}, 4, "other", [])
            staged = stale.stage(old_plan)
            new = stale.ingest([from_text("再慢 4 BPM。", session_id="preempted", event_id="new")])
            superseding_state = stale.state
            errors = [reject(lambda: OfflineRuntime().execute(stale, old_plan)),
                      reject(lambda: stale.stage(duplicate))]
            untouched = stale.state == superseding_state
            fresh = compile_jev_request(new, superseding_state, "new", tempo=("down", 4))
            stale.stage(fresh)
            OfflineRuntime().execute(stale, fresh)
            checks = {"original_was_pending": staged, "obsolete_execute_and_restaging_rejected": all(errors),
                      "obsolete_attempts_unchanged": untouched, "stale_status": stale.plan_status("old") == "cancelled",
                      "fresh_apply_once": stale.state.tempo_bpm == original.tempo_bpm - 4
                      and stale.state.position.next_bar == 4 and not stale.stage(fresh)}
        else:
            events = manager.ingest([from_text("再慢 4 BPM，生成四小节。", session_id=manager.state.session_id,
                                              event_id="jev")])
            state = manager.state
            omitted = compile_jev_request(events, state, "omit-bars", tempo=("down", 4))
            rejected_unchanged = omitted is None and state == manager.state
            plan = compile_jev_request(events, state, "jev-after-commit", tempo=("down", 4), bars=4)
            staged = should_execute(state, plan) and manager.stage(plan)
            clip = OfflineRuntime().execute(manager, plan)
            current = state_summary(manager.state)
            checks = {"missing_slot_abstains_without_mutation": rejected_unchanged, "candidate_applies": staged,
                      "tempo": current["bpm"] == case["tempo"], "next_bar": current["next_bar"] == case["bar"],
                      "keys": current["layers"]["keys"]["active"] == case["keys"],
                      "drums": current["layers"]["drums"]["active"] == case["drums"],
                      "keys_absent_in_clip": all(note.instrument != "keys" for note in clip.notes)}
            errors = []
        rows.append({"case": case_id, "mode": "direct", "expected_outcome": case["outcome"],
                     "errors": errors, "checks": checks, "passed": all(checks.values())})
    return rows


def summarize(rows):
    result = {}
    for mode in sorted({row["mode"] for row in rows}):
        mode_rows = [row for row in rows if row["mode"] == mode]
        evidence = [call for row in mode_rows for call in row.get("provider_calls", [])]
        result[mode] = {"cases": len(mode_rows), "passed": sum(row["passed"] for row in mode_rows),
                        "failures": {row["case"]: [check for check, value in row["checks"].items() if not value]
                                     for row in mode_rows if not row["passed"]},
                        "outcomes": dict(Counter(row["outcome"] for row in mode_rows if "outcome" in row)),
                        "provider_attempts": len(evidence), "models": sorted({call["model"] for call in evidence
                                                                             if "model" in call})}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--config", type=Path, help="Also run the three live requests using this private LLM config.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    fixture = Path(__file__).with_name("classifier_round4_cases.json").read_bytes()
    cases = json.loads(fixture)
    digest = sha256(fixture).hexdigest()
    if args.dry_run:
        print(json.dumps({"fixture_sha256": digest, **{mode: len(cases[mode]) for mode in ("scripted", "direct", "live")}}))
        return
    if args.out is None:
        parser.error("--out is required")
    args.out.mkdir(parents=True, exist_ok=False)
    rows, manager = run_worker(cases, "scripted", LLMConfig(api_key="scripted"), RecordingProvider())
    rows.extend(run_direct(cases, manager))
    if args.config:
        config = replace(LLMConfig.load(args.config, environ={}), model="gpt-5.4-mini", reasoning_effort="low")
        live, _ = run_worker(cases, "live", config, RecordingProvider(OpenAIProvider(config)))
        rows.extend(live)
    summary = {"fixture_sha256": digest, "results": summarize(rows)}
    with (args.out / "cases.jsonl").open("x", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
