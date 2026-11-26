"""Offline reference executor for BandPlan, reusing the existing bar arranger.

A receipt means notes/audio were produced in memory, not that a sound card
played them. Live scheduling and external renderer adapters remain separate.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field, replace

import numpy as np

from jammate.contracts.band_plan import BandPlan, PlanError
from jammate.generate.arranger import arrange_bar, chord_for_bar, place_lead
from jammate.generate.response import answer_motif, continue_motif, initiate_motif
from jammate.interaction import Motif
from jammate.music import BEATS_PER_BAR, Note
from jammate.render.synth import render, TAIL_S
from jammate.state import JamState
from .manager import StateManager
from .transitions import project


@dataclass(frozen=True)
class PlannedPerformance:
    notes: tuple[Note, ...]  # absolute session beats
    lead_notes: tuple[Note, ...]  # canonical phrase, relative to plan start
    generated_motif: Motif | None
    tempo_bpm: float
    start_bar: int
    bars: int
    beats_per_bar: int = field(default=BEATS_PER_BAR, kw_only=True)


@dataclass(frozen=True)
class RenderedPlan(PlannedPerformance):
    audio: np.ndarray
    sample_rate: int


def apply_layer_settings(notes, state, *, bar=0):
    output = []
    for layer in state.layers:
        if not layer.active or layer.level == 0 or layer.density == 0:
            continue
        group = [n for n in notes if n.instrument == layer.name]
        # Reduce accompaniment onset groups together so a chord remains a chord.
        # Lead density is deliberately unsupported: never silently drop motif notes.
        onsets = sorted({n.start for n in group})
        density = layer.density if layer.name != "lead" else 1.
        # A single chord onset cannot be thinned within its bar. Spread its
        # rests over the session's absolute bars; keep all chord tones together.
        # This phase is independent of how plans partition the same timeline.
        count = max(1, math.floor(len(onsets) * density + 1e-9)) if onsets else 0
        if len(onsets) == 1 and density < 1:
            count = math.floor((bar + 1) * density + 1e-9) - math.floor(bar * density + 1e-9)
        chosen = {onsets[min(len(onsets) - 1, int(i * len(onsets) / count))] for i in range(count)} if count else set()
        output.extend(replace(n, velocity=min(127, max(1, round(n.velocity * layer.level / .5))))
                      for n in group if n.start in chosen)
    return tuple(sorted(output, key=lambda n: (n.start, n.instrument, n.pitch)))


def prepare_bar(state: JamState, bar: int, lead: tuple[Note, ...] = ()) -> tuple[Note, ...]:
    """Arrange one bar and apply the shared layer settings."""
    return apply_layer_settings(arrange_bar(state, bar, lead), state, bar=bar)


def prepare_humming_answer(state: JamState, motif: Motif) -> tuple[Note, ...]:
    key = state.key
    if key is None:
        raise PlanError("哼唱补全需要一个可用的调性。")
    anchor = max(motif.notes, key=lambda note: note.end).pitch
    lowest = min(note.pitch for note in motif.notes)
    low, high = min(115, max(0, anchor - 7, lowest - 2)), min(127, anchor + 12)
    answer = continue_motif(motif.notes, key, low=low, high=high)
    start = int(answer[0].start // state.beats_per_bar)
    end = math.ceil(max(note.end for note in answer) / state.beats_per_bar)
    backing = tuple(note for bar in range(start, end) for note in prepare_bar(state, bar)
                    if note.instrument not in {"drums", "lead"})
    scale = tuple(pitch for pitch in range(low, high + 1) if key.contains(pitch))
    result = []
    for index, note in enumerate(answer):
        sounding = {other.pitch for other in (*backing, *result)
                    if min(note.end, other.end) - max(note.start, other.start) >= .05}
        chord = chord_for_bar(state, int(note.start // state.beats_per_bar))
        chord_tones = tuple(pitch for pitch in scale if chord and pitch % 12 in chord.pcs)
        safe = tuple(pitch for pitch in scale if all(abs(pitch - other) > 1 for other in sounding))
        anchors = tuple(pitch for pitch in chord_tones if pitch in safe)
        options = (anchors or safe or chord_tones or scale) if index in (0, len(answer) - 1) else (safe or scale)
        aim = anchor if index == 0 else note.pitch
        result.append(replace(note, pitch=min(options, key=lambda pitch: (abs(aim - pitch), pitch))))
    return tuple(result)


def performance_state_for_bar(state, target, actions, offset, bars):
    layers = []
    for layer in target.layers:
        build = next((a for a in actions if a["action"] == "BUILD" and a["scope"] in {"band", layer.name}), None)
        if build:
            fraction = min(1., (offset + 1) / build["parameters"].get("duration_bars", bars))
            previous = next((item.level for item in state.layers if item.name == layer.name), .5)
            level = previous + (layer.level - previous) * fraction
            layer = replace(layer, level=level)
        layers.append(layer)
    return target.with_updates(layers=tuple(layers))


def prepare_plan(state: JamState, plan: BandPlan, *, seed=3800, opening_answer: tuple[Note, ...] = ()) -> PlannedPerformance:
    """Compile/validate actual notes without synthesizing audio or changing state."""
    target, data = project(state, plan), plan.to_dict()
    start, bars = data["effective_at"]["bar"], data["valid_for_bars"]
    if start != state.position.next_bar:
        raise PlanError("offline executor requires the next unrendered bar; cannot skip intervening bars")
    lead, generated = (), None
    if target.playing and target.auto_harmony and target.key is None:
        raise PlanError("baseline accompaniment needs an explicitly configured session key")
    rng = random.Random(int.from_bytes(hashlib.sha256(f"{seed}:{plan.plan_id}".encode()).digest()[:8], "big"))
    for action in data["actions"]:
        name, params = action["action"], action["parameters"]
        if target.key is None and name in {"VARY_MOTIF", "INTRODUCE_MOTIF"}:
            raise PlanError("需要先指定调性才能生成或变奏旋律。")
        if name == "ECHO_MOTIF":
            lead = tuple(replace(n, instrument="lead") for n in target.motif(params["motif_id"]).notes)
        elif name == "VARY_MOTIF":
            lead = answer_motif(target.motif(params["motif_id"]).notes, target.key, rng).notes
        elif name == "INTRODUCE_MOTIF":
            lead = initiate_motif(target.key, rng)
            generated = Motif(params["motif_id"], "ai", tuple(data["trigger_event_ids"]), lead)
    if lead and (not target.playing or not target.layer("lead").active or target.layer("lead").level == 0):
        raise PlanError("motif response requires an audible lead layer")
    if any(n.end > bars * target.beats_per_bar for n in lead):
        raise PlanError("generated motif extends past plan lifetime")
    performance_bars = bars
    if opening_answer:
        if (start != 0 or len(data["actions"]) != 1 or data["actions"][0]["action"] != "ECHO_MOTIF"
                or not lead or opening_answer[0].start < max(note.end for note in lead)):
            raise PlanError("opening continuation must follow the replayed melody")
        performance_bars = max(bars, math.ceil(max(note.end for note in opening_answer) / target.beats_per_bar))
    lead_bars = place_lead(lead + opening_answer, start, target.beats_per_bar)
    all_notes = []
    violations = set()
    for offset in range(performance_bars):
        bar = start + offset
        bar_state = performance_state_for_bar(state, target, data["actions"], offset, bars)
        notes = prepare_bar(bar_state, bar, lead_bars.get(bar, ()))
        # A preserved layer must also keep actual notes/velocities, not just metadata.
        # Explicit start/stop governs audibility globally. Compare preserved
        # arrangements under that same transport control, including a restart.
        reference = state.with_updates(playing=target.playing)
        baseline = prepare_bar(reference, bar) if reference.playing and (reference.key is not None or not reference.auto_harmony) else ()
        for preserved in data["preserve"]:
            if preserved.startswith("layer:"):
                instrument = preserved[6:]
                if tuple(n for n in baseline if n.instrument == instrument) != tuple(n for n in notes if n.instrument == instrument):
                    violations.add(preserved)
            elif preserved == "groove":
                signature = lambda ns: [(n.pitch, n.start, n.duration) for n in ns if n.instrument == "drums"]
                if signature(baseline) != signature(notes):
                    violations.add("groove")
        all_notes.extend(notes)
    if violations:
        raise PlanError("render would change preserved " + ", ".join(sorted(violations)))
    notes = tuple(all_notes)
    return PlannedPerformance(notes, lead, generated, target.tempo_bpm, start, performance_bars,
                              beats_per_bar=target.beats_per_bar)


def render_plan(state: JamState, plan: BandPlan, *, seed=3800, sample_rate=22050) -> RenderedPlan:
    return _render_performance(prepare_plan(state, plan, seed=seed), sample_rate)


def _render_performance(performance, sample_rate):
    local = tuple(n.shifted(-performance.start_bar * performance.beats_per_bar) for n in performance.notes)
    audio = render(local, performance.tempo_bpm, sample_rate,
                   total_s=performance.bars * performance.beats_per_bar * 60 / performance.tempo_bpm + TAIL_S)
    return RenderedPlan(performance.notes, performance.lead_notes, performance.generated_motif,
                        performance.tempo_bpm, performance.start_bar, performance.bars, audio, sample_rate,
                        beats_per_bar=performance.beats_per_bar)


class OfflineRuntime:
    def __init__(self, *, seed=3800, sample_rate=22050, arranger=None, backend_arranger=None):
        self.seed, self.sample_rate = seed, sample_rate
        self.arranger = arranger
        self.backend_arranger = backend_arranger

    def validate(self, state, plan):
        if self.backend_arranger is not None:
            return project(state, plan)
        return prepare_plan(state, plan, seed=self.seed)

    def execute(self, manager: StateManager, plan: BandPlan) -> RenderedPlan:
        state, events = manager.execution_snapshot(plan)
        data = plan.to_dict()
        bounds = {"effective_bar": data["effective_at"]["bar"],
                  "completed_bar": data["effective_at"]["bar"] + data["valid_for_bars"]}
        try:
            # Validation/preflight must never spend an extra model request.
            if self.backend_arranger is not None:
                from .backend_arrangement import prepare_scoped_score
                performance, _ = prepare_scoped_score(state, plan, events, self.backend_arranger, seed=self.seed)
                manager.check_pending(plan)
                result = _render_performance(performance, self.sample_rate)
            elif self.arranger is None:
                result = render_plan(state, plan, seed=self.seed, sample_rate=self.sample_rate)
            else:
                performance = self.validate(state, plan)
                performance = self.arranger.arrange(state, plan, performance, events)
                manager.check_pending(plan)
                result = _render_performance(performance, self.sample_rate)
        except Exception as exc:
            if manager.plan_status(plan.plan_id) == "pending":
                manager.acknowledge(plan, succeeded=False, reason=str(exc), **bounds)
            raise
        accepted = manager.acknowledge(plan, succeeded=True, lead_notes=result.lead_notes,
                                       generated_motif=result.generated_motif, **bounds)
        if not accepted:
            raise PlanError("execution receipt was not accepted; plan already reached a terminal status")
        return result
