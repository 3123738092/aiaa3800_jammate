"""Compare the SAME event/state and saved plan across FluidSynth and MRT2.

Run from the full checkout, not an installed wheel alone. LLM mode really calls
the configured provider; normal unit tests substitute that provider explicitly.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jammate.contracts.adapters import from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.render.runtime import create_runtime
from jammate.state import JamState


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", choices=("rule", "llm", "both"), default="rule")
    p.add_argument("--renderer", choices=("fluidsynth", "mrt2", "both"), default="both")
    p.add_argument("--text", default="鼓更猛一点")
    p.add_argument("--config", type=Path)
    p.add_argument("--fluidsynth", type=Path)
    p.add_argument("--soundfont", type=Path)
    p.add_argument("--allow-preview", action="store_true")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    initial = JamState(session_id="backend_comparison", tempo_bpm=100.)
    event = from_text(args.text, session_id=initial.session_id, event_id="comparison_input")
    event.write(args.out / "input.json")
    (args.out / "state_initial.json").write_text(json.dumps(initial.to_dict(), indent=2)+"\n")
    agents = ("rule", "llm") if args.agent == "both" else (args.agent,)
    renderers = ("fluidsynth", "mrt2") if args.renderer == "both" else (args.renderer,)
    report = {"input": event.to_dict(), "results": [], "limitations":
              "Offline export comparison, not blinded listening or microphone/real-time acceptance."}
    for mode in agents:
        manager = StateManager(initial)
        events = manager.ingest([event])
        agent = RuleAgent() if mode == "rule" else LLMAgent(LLMConfig.load(args.config), fallback="error")
        plan = agent.decide(events, manager.state, plan_id=f"comparison_{mode}")
        plan.write(args.out / f"{mode}.plan.json")
        trace = getattr(agent, "last_trace", {"source": "rule"})
        (args.out / f"{mode}.decision.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2)+"\n")
        for backend in renderers:
            # A fresh identical state per backend: never let one renderer's
            # acknowledgement change the other renderer's input.
            m = StateManager(initial)
            m.ingest([event])
            runtime = create_runtime(args.out / f"{mode}_{backend}", backend=backend,
                fluidsynth=args.fluidsynth, soundfont=args.soundfont, allow_preview=args.allow_preview)
            row = {"agent": mode, "backend": backend, "plan_id": plan.plan_id}
            try:
                m.stage(plan)
                runtime.execute(m, plan)
                row["receipt"] = runtime.last_receipt
            except Exception as error:
                # Provider exceptions are deliberately not printed: error text
                # from third-party clients can contain credentials/endpoints.
                row.update(error=type(error).__name__, receipt=runtime.last_receipt)
            row["state_after"] = m.state.to_dict()
            report["results"].append(row)
            (args.out / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps([{k: v for k, v in row.items() if k != "state_after"} for row in report["results"]], indent=2))


if __name__ == "__main__":
    main()
