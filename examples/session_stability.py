"""Controlled failure soak or a real continuous LLM + Kevin conversation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from jammate.contracts import PlanError, InputEvent
from jammate.contracts.adapters import from_text, from_system
from jammate.orchestration.llm import LLMAgent, ModelReply, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_plan
from jammate.state import JamState
from run_acceptance_batch import write, check_expectation, value


def acknowledge(manager, plan, succeeded=True, performance=None):
    data = plan.to_dict()
    return manager.acknowledge(plan, succeeded=succeeded, effective_bar=data["effective_at"]["bar"],
        completed_bar=data["effective_at"]["bar"] + data["valid_for_bars"],
        lead_notes=performance.lead_notes if performance else (), generated_motif=performance.generated_motif if performance else None)


class ControlledProvider:
    def __init__(self):
        self.fail_next = None
        self.calls = 0

    def complete(self, messages, schema, timeout_s):
        self.calls += 1
        fault, self.fail_next = self.fail_next, None
        if fault == "connection":
            raise ProviderFailure("connection")
        if fault == "invalid":
            return ModelReply("{", "controlled-invalid", {})
        return ModelReply(json.dumps({"strategy": "FOLLOW", "valid_for_bars": 1, "actions": [],
            "preserve": ["tempo", "key", "playing"], "transport": {"playing": None, "tempo_bpm": None},
            "reason": "controlled hold for infrastructure testing", "unresolved": []}), "controlled", {})


class FaultLog:
    broken = False
    def write(self, *_args, **_kwargs):
        if self.broken:
            raise OSError(28, "injected disk full")


def controlled(args):
    args.out.mkdir(parents=True, exist_ok=False)
    source = json.loads((ROOT / "examples/input_events/humming.json").read_text(encoding="utf-8"))
    log, provider = FaultLog(), ControlledProvider()
    manager = StateManager(JamState(session_id="soak", tempo_bpm=100), log=log)
    agent = LLMAgent(LLMConfig("controlled-test-only"), provider=provider)
    report = {"mode": "controlled_fault_injection", "real_api_calls": 0, "wav_rendered": False,
              "rounds": args.rounds, "protocol": {"duplicate_event": "every turn", "duplicate_receipt": "every turn",
              "export_failure": "every 7 turns", "replacement": "every 11 turns unless export failure",
              "connection_failure": "every 13 turns", "invalid_response": "every 17 turns unless connection failure",
              "stale_event": "every 19 turns", "log_failure": "every 23 turns", "new_humming": "every 3 turns"}, "turns": []}
    write(args.out / "protocol.json", {k: v for k, v in report.items() if k != "turns"})
    tracemalloc.start()
    start = time.monotonic()
    for i in range(1, args.rounds + 1):
        started = time.monotonic()
        before_version, before_bar = manager.state.state_version, manager.state.position.next_bar
        log.broken = i % 23 == 0
        batch = [from_text("保持当前伴奏，不要回应旋律。", session_id="soak", event_id=f"t{i}", started_at=i, ended_at=i)]
        if i % 3 == 0:
            batch.append(InputEvent.from_dict({**source, "session_id": "soak", "event_id": f"h{i}", "started_at": None, "ended_at": None}))
        events = manager.ingest(batch)
        assert not manager.ingest(batch)
        if i % 19 == 0:
            assert not manager.ingest([from_text("旧的停止", session_id="soak", event_id=f"stale{i}", started_at=i-1, ended_at=i-1)])
        provider.fail_next = "connection" if i % 13 == 0 else "invalid" if i % 17 == 0 else None
        state, history = manager.decision_snapshot()
        plan = agent.decide(events, state, plan_id=f"p{i}", history=history)
        first_trace = dict(agent.last_trace)
        manager.stage(plan)
        failed, replaced = False, False
        if i % 7 == 0:
            acknowledge(manager, plan, False)
            failed = True
            state, history = manager.decision_snapshot()
            plan = agent.decide(manager.current_batch(), state, plan_id=f"retry{i}", history=history)
            manager.stage(plan)
        elif i % 11 == 0:
            obsolete = plan
            plan = agent.decide(events, state, plan_id=f"replacement{i}", history=history)
            manager.stage(plan)
            replaced = True
            try:
                acknowledge(manager, obsolete)
            except PlanError:
                pass
            else:
                raise AssertionError("obsolete receipt applied")
        performance = prepare_plan(manager.state, plan)
        assert acknowledge(manager, plan, performance=performance)
        assert not acknowledge(manager, plan, performance=performance)
        assert manager.state.state_version == before_version + 2
        assert manager.state.position.next_bar == before_bar + 1
        assert manager.state.layers == JamState().layers
        assert not manager.current_batch()
        memory, peak = tracemalloc.get_traced_memory()
        row = {"turn": i, "source": first_trace["source"], "failure": first_trace.get("failure"),
               "attempts": len(first_trace["attempts"]), "request_chars": first_trace.get("request_chars"),
               "version": manager.state.state_version, "next_bar": manager.state.position.next_bar,
               "motifs": len(manager.state.motifs), "failed_export_recovered": failed, "replaced_plan": replaced,
               "memory_bytes": memory, "peak_memory_bytes": peak, "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
        report["turns"].append(row)
        if i % 25 == 0:
            print(json.dumps(row), flush=True)
    tracemalloc.stop()
    report.update(passed=True, elapsed_s=round(time.monotonic()-start, 3), provider_calls=provider.calls,
                  diagnostics=manager.diagnostics(), final_state_version=manager.state.state_version)
    write(args.out / "report.json", report)
    print(json.dumps({k:v for k,v in report.items() if k not in {"turns", "protocol"}}), flush=True)


def real_llm(args):
    from agent_demo import run
    from renderer_experiments.app.band_plan import KevinRuntime
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    batches = []
    for i, case in enumerate(cases["turns"], 1):
        metadata = {"session_id": cases["session_id"], "event_id": f"event_{i:02d}"}
        event = from_text(case["text"], **metadata) if "text" in case else from_system(
            case["command"], **metadata, **{k:case[k] for k in ("bars", "active") if k in case})
        batches.append([event])
    config = LLMConfig.load(args.config)
    runtime = KevinRuntime(args.out / "renderer", executable=args.fluidsynth, soundfont=args.soundfont)
    agent = LLMAgent(config, execution_validator=runtime.validate)
    run(batches, args.out, tempo_bpm=cases["initial_bpm"], agent=agent, runtime=runtime)
    after_states, results = [], []
    for i, case in enumerate(cases["turns"], 1):
        folder = args.out / f"turn_{i:02d}"
        read = lambda name: json.loads((folder / name).read_text(encoding="utf-8"))
        from jammate.contracts import BandPlan
        before, after = JamState.from_dict(read("state_observed.json")), JamState.from_dict(read("state_applied.json"))
        plan, trace = BandPlan.from_dict(read("plan.json")), read("agent_decision.json")
        expected = case["expect"]
        # Decode actual saved MIDI onset counts for density checks.
        from replay_acceptance_renderer import read_performance
        performance = read_performance(folder / "band.mid", plan, after.tempo_bpm)
        failures = check_expectation(before, after, plan, expected, performance=performance)
        if "same_as_turn" in expected:
            old = expected["same_as_turn"]
            for path in old["paths"]:
                if value(after, path) != value(after_states[old["turn"]-1], path):
                    failures.append("history restore failed: " + path)
        data = plan.to_dict()
        if expected.get("strategy") and data["strategy"] != expected["strategy"]:
            failures.append("unexpected strategy")
        if expected.get("no_motif") and any(a["action"].endswith("MOTIF") for a in plan.actions):
            failures.append("unexpected motif response")
        if "awaiting_user" in expected and after.interaction.awaiting_user != expected["awaiting_user"]:
            failures.append("awaiting_user mismatch")
        if expected.get("action") and not any(a["action"] == expected["action"] for a in plan.actions):
            failures.append("expected action missing")
        if trace["source"] != expected.get("decision_source", "llm"):
            failures.append("unexpected decision source")
        after_states.append(after)
        results.append({"turn":i, "passed":not failures, "failures":failures, "source":trace["source"],
                        "request_chars":trace.get("request_chars"), "decision_ms":trace.get("elapsed_ms"),
                        "attempts":len(trace["attempts"])})
    report = {"mode":"continuous_real_llm_kevin_offline", "cases_sha256":hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              "passed":sum(r["passed"] for r in results), "total":len(results), "turns":results,
              "limitations":"One development conversation; no human recordings or physical playback acceptance."}
    write(args.out / "evaluation.json", report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("controlled","llm"), default="controlled")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=300)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("stability_cases.json"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fluidsynth", type=Path)
    parser.add_argument("--soundfont", type=Path)
    args = parser.parse_args()
    if args.mode == "controlled":
        if not 1 <= args.rounds <= 1000:
            parser.error("rounds must be within 1-1000")
        controlled(args)
    else:
        if not args.fluidsynth or not args.soundfont:
            parser.error("LLM mode requires --fluidsynth and --soundfont")
        real_llm(args)
