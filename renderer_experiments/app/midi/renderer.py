from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import MusicIntent
from .events import MidiEvent
from .patterns import render_pattern

class MidiPort(Protocol):
    def send(self, message: Any) -> None: ...
    def close(self) -> None: ...

@dataclass
class MidiMetrics:
    dispatched: int = 0
    late_events: int = 0
    jitters_ms: list[float] = field(default_factory=list)
    active_notes: set[tuple[int, int]] = field(default_factory=set)
    all_notes_off_sent: int = 0
    def as_dict(self) -> dict[str, Any]:
        values = self.jitters_ms
        return {"dispatched": self.dispatched, "late_events": self.late_events, "all_notes_off_sent": self.all_notes_off_sent,
                "active_notes": len(self.active_notes), "jitter_mean_ms": statistics.mean(values) if values else 0,
                "jitter_median_ms": statistics.median(values) if values else 0,
                "jitter_p95_ms": sorted(values)[max(0, int(len(values) * .95) - 1)] if values else 0,
                "jitter_max_ms": max(values, default=0)}

class MidiRenderer:
    def __init__(self, port: MidiPort | None = None, late_threshold_ms: float = 20.0):
        self.port = port
        self.late_threshold_ms = late_threshold_ms
        self.metrics = MidiMetrics()
        self.intent: MusicIntent | None = None
        self.events: list[MidiEvent] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def connect(self, port_name: str | None = None) -> None:
        if self.port is not None: return
        try:
            import mido
            self.port = mido.open_output(port_name) if port_name else mido.open_output()
        except ImportError as exc: raise RuntimeError("mido and python-rtmidi are required for MIDI output") from exc

    def start(self, intent: MusicIntent, bars: int = 2) -> None:
        self.intent = intent
        self.events = render_pattern(intent, bars)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _send(self, event: MidiEvent) -> None:
        if self.port is None: return
        import mido
        if event.type == "all_notes_off":
            self.port.send(mido.Message("control_change", channel=event.channel, control=123, value=0)); return
        if event.type in ("note_on", "note_off"):
            self.port.send(mido.Message(event.type, channel=event.channel, note=event.note, velocity=event.velocity))
        elif event.type == "control_change":
            self.port.send(mido.Message("control_change", channel=event.channel, control=event.control, value=event.value))

    def _run(self) -> None:
        origin = time.monotonic()
        for event in self.events:
            target = origin + event.timestamp
            while not self._stop.is_set() and time.monotonic() < target: time.sleep(min(.002, target - time.monotonic()))
            if self._stop.is_set(): break
            jitter = (time.monotonic() - target) * 1000
            self.metrics.jitters_ms.append(jitter); self.metrics.dispatched += 1
            if jitter > self.late_threshold_ms: self.metrics.late_events += 1
            self._send(event)
            if event.type == "note_on": self.metrics.active_notes.add((event.channel, event.note))
            elif event.type == "note_off": self.metrics.active_notes.discard((event.channel, event.note))
        self.stop()

    def update(self, intent: MusicIntent) -> None:
        self.stop(); self.start(intent)
    def pause(self) -> None: self._stop.set()
    def resume(self) -> None:
        if self.intent: self.start(self.intent)
    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=1)
        if self.port:
            for channel in range(16): self._send(MidiEvent(0, "all_notes_off", channel=channel))
            self.metrics.all_notes_off_sent += 16
        self.metrics.active_notes.clear()
    def reset(self) -> None: self.stop(); self.events = []
