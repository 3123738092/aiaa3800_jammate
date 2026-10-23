import json
from datetime import datetime, timezone

import pytest

from jammate.director.policy import Decision
from jammate.music import Note
from jammate.session_log import SessionLog, decision_record
from jammate.state import Action, JamState

pytestmark = pytest.mark.unit

FIXED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_decision_record_is_json_friendly():
    decision = Decision(Action.FOLLOW, JamState(), (Note(72, 0.0, 1.0),), "answered")
    record = decision_record(decision)
    assert record == {
        "action": "follow", "reason": "answered", "tempo_bpm": 96.0, "key": "C major",
        "energy": 1, "playing": True, "lead": [[72, 0.0, 1.0]],
    }
    json.dumps(record)


def test_log_appends_json_lines_with_timestamps(tmp_path):
    path = tmp_path / "logs" / "session.jsonl"
    log = SessionLog(path, clock=lambda: FIXED)
    log.write("phrase", notes=3)
    log.write("decision", action="hold")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines] == ["phrase", "decision"]
    assert lines[0]["t"] == "2026-01-01T00:00:00.000000+00:00"
    assert lines[0]["notes"] == 3
    assert lines[1]["monotonic_ns"] >= lines[0]["monotonic_ns"]


def test_log_without_path_only_returns_records():
    record = SessionLog(None, clock=lambda: FIXED).write("phrase", notes=1)
    assert record["type"] == "phrase"
