"""Append-only JSONL log of what the human did and how the band answered.

This is the raw data for the user study (response timing, how often users
steer the band, which AI moves they keep), so every decision is logged with
its explanation.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from jammate.director.policy import Decision


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class SessionLog:
    """Thread-safe JSONL appender; with `path=None` it only builds records."""

    def __init__(self, path: Path | None, clock: Callable[[], datetime] = _now) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._lock = threading.Lock()  # live mode writes from several threads

    def write(self, event_type: str, **fields: object) -> dict[str, object]:
        with self._lock:
            record: dict[str, object] = {
                "t": self._clock().isoformat(timespec="seconds"), "type": event_type, **fields}
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            return record


def decision_record(decision: Decision) -> dict[str, object]:
    state = decision.state
    return {
        "action": decision.action.value,
        "reason": decision.reason,
        "tempo_bpm": round(state.tempo_bpm, 2),
        "key": state.key.name,
        "energy": state.energy,
        "playing": state.playing,
        "lead": [[n.pitch, n.start, n.duration] for n in decision.lead],
    }
