"""Render a real JamMate plan with the demo generator."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from jammate.contracts.adapters import from_system, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.backend_arrangement import LLMBackendArranger
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.render.runtime import create_runtime
from jammate.state import JamState

from .jammate_bridge import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--text', default='鼓更猛一点')
    inputs.add_argument('--stop', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--bpm', type=float, default=96)
    parser.add_argument('--allow-preview', action='store_true')
    parser.add_argument('--arrangement', choices=('rule', 'llm'), default='rule')
    parser.add_argument('--config', type=Path, help='JamMate LLM config for direct frame arrangement')
    args = parser.parse_args()

    manager = StateManager(JamState(session_id=f'mrt2-demo-{uuid4().hex}', tempo_bpm=args.bpm))
    event = (from_system('stop', session_id=manager.state.session_id) if args.stop
             else from_text(args.text, session_id=manager.state.session_id))
    events = manager.ingest([event])
    plan = RuleAgent().decide(events, manager.state, plan_id='demo')
    if plan.to_dict()['unresolved']:
        parser.error(', '.join(plan.to_dict()['unresolved']))
    manager.stage(plan)
    arranger = (LLMBackendArranger(LLMConfig.load(args.config), 'mrt2')
                if args.arrangement == 'llm' else None)
    runtime = create_runtime(args.out, backend='mrt2', allow_preview=args.allow_preview,
                             backend_arranger=arranger, generator=generate)
    try:
        runtime.execute(manager, plan)
    finally:
        if runtime.last_receipt is not None:
            print(json.dumps({'artifacts': str(runtime.last_artifacts), **runtime.last_receipt},
                             ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
