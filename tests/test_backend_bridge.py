"""Process-loading integration; no external LLM or MRT2 weights involved."""
from pathlib import Path

import pytest

from jammate.reference_bridge import ReferenceBridge


ROOT = Path(__file__).resolve().parents[1]


def request(**changes):
    return {"session_id": "backend-bridge", "request_id": "one", "after_turn": 0,
            "text": "鼓更猛一点", "agent": "rule", "bpm": 100, "profile": "electric", **changes}


def test_workspace_bridge_loads_new_adapter_instead_of_pinned_snapshot(tmp_path):
    with ReferenceBridge(ROOT, scratch=tmp_path, implementation="workspace") as bridge:
        assert bridge._snapshot() == ROOT
        with pytest.raises(ValueError, match="MRT2 cannot guarantee") as error:
            bridge.request("text/turn", request(renderer="mrt2"))
        assert error.value.report["state_commit_allowed"] is False
        assert {item["constraint"] for item in error.value.report["preserve"]} >= {"tempo", "key"}
        # Strict backend rejection leaves the worker usable for another work.
        result = bridge.request("text/turn", request(session_id="exact", renderer="default"))
        assert result["implementation"] == "workspace" and result["reference_commit"] is None
        assert result["renderer"] == "原版合成器"
        assert result["wav"] and result["midi"]


def test_pinned_bridge_explicitly_refuses_unavailable_new_backend(tmp_path):
    with ReferenceBridge(ROOT, scratch=tmp_path, implementation="pinned") as bridge:
        with pytest.raises(ValueError, match="implementation workspace"):
            bridge.request("text/turn", request(renderer="mrt2"))
