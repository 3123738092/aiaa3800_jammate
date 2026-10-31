"""Offline reference executor for BandPlan, reusing the existing bar arranger.

A receipt means notes/audio were produced in memory, not that a sound card
played them. Live scheduling and external renderer adapters remain separate.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, replace

import numpy as np

from jammate.contracts.band_plan import BandPlan, PlanError
from jammate.generate.arranger import arrange_bar, place_lead
from jammate.generate.response import answer_motif, initiate_motif
from jammate.interaction import Motif
from jammate.music import Note
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


@dataclass(frozen=True)
class RenderedPlan(PlannedPerformance):
    audio: np.ndarray
    sample_rate: int


def _layers(notes, state, *, bar=0):
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


def prepare_plan(state: JamState, plan: BandPlan, *, seed=3800) -> PlannedPerformance:
    """Compile/validate actual notes without synthesizing audio or changing state."""
    target, data = project(state, plan), plan.to_dict()
    start, bars = data["effective_at"]["bar"], data["valid_for_bars"]
    if start != state.position.next_bar:
        raise PlanError("offline executor requires the next unrendered bar; cannot skip intervening bars")
    lead, generated = (), None
    if target.playing and target.key is None:
        raise PlanError("baseline accompaniment needs an explicitly configured session key")
    rng = random.Random(int.from_bytes(hashlib.sha256(f"{seed}:{plan.plan_id}".encode()).digest()[:8], "big"))
    for action in data["actions"]:
        name, params = action["action"], action["parameters"]
        if name == "ECHO_MOTIF":
            lead = tuple(replace(n, instrument="lead") for n in target.motif(params["motif_id"]).notes)
        elif name == "VARY_MOTIF":
            lead = answer_motif(target.motif(params["motif_id"]).notes, target.key, rng).notes
        elif name == "INTRODUCE_MOTIF":
            lead = initiate_motif(target.key, rng)
            generated = Motif(params["motif_id"], "ai", tuple(data["trigger_event_ids"]), lead)
    if lead and (not target.playing or not target.layer("lead").active or target.layer("lead").level == 0):
        raise PlanError("motif response requires an audible lead layer")
    if any(n.end > bars * 4 for n in lead):
        raise PlanError("generated motif extends past plan lifetime")
    lead_bars = place_lead(lead, start)
    all_notes = []
    violations = set()
    for offset in range(bars):
        bar = start + offset
        layers = []
        for layer in target.layers:
            build = next((a for a in data["actions"] if a["action"] == "BUILD" and a["scope"] in {"band", layer.name}), None)
            if build:
                fraction = min(1., (offset + 1) / build["parameters"].get("duration_bars", bars))
                level = state.layer(layer.name).level + (layer.level - state.layer(layer.name).level) * fraction
                layer = replace(layer, level=level)
            layers.append(layer)
        bar_state = target.with_updates(layers=tuple(layers))
        notes = _layers(arrange_bar(bar_state, bar, lead_bars.get(bar, ())), bar_state, bar=bar)
        # A preserved layer must also keep actual notes/velocities, not just metadata.
        # Explicit start/stop governs audibility globally. Compare preserved
        # arrangements under that same transport control, including a restart.
        reference = state.with_updates(playing=target.playing)
        baseline = _layers(arrange_bar(reference, bar), reference, bar=bar) if reference.playing and reference.key is not None else ()
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
    return PlannedPerformance(notes, lead, generated, target.tempo_bpm, start, bars)


def render_plan(state: JamState, plan: BandPlan, *, seed=3800, sample_rate=22050) -> RenderedPlan:
    performance = prepare_plan(state, plan, seed=seed)
    local = tuple(n.shifted(-performance.start_bar * 4) for n in performance.notes)
    audio = render(local, performance.tempo_bpm, sample_rate,
                   total_s=performance.bars * 4 * 60 / performance.tempo_bpm + TAIL_S)
    return RenderedPlan(performance.notes, performance.lead_notes, performance.generated_motif,
                        performance.tempo_bpm, performance.start_bar, performance.bars, audio, sample_rate)


class OfflineRuntime:
    def __init__(self, *, seed=3800, sample_rate=22050):
        self.seed, self.sample_rate = seed, sample_rate

    def validate(self, state, plan):
        return prepare_plan(state, plan, seed=self.seed)

    def execute(self, manager: StateManager, plan: BandPlan) -> RenderedPlan:
        state = manager.check_pending(plan)
        data = plan.to_dict()
        bounds = {"effective_bar": data["effective_at"]["bar"],
                  "completed_bar": data["effective_at"]["bar"] + data["valid_for_bars"]}
        try:
            result = render_plan(state, plan, seed=self.seed, sample_rate=self.sample_rate)
        except Exception as exc:
            if manager.plan_status(plan.plan_id) == "pending":
                manager.acknowledge(plan, succeeded=False, reason=str(exc), **bounds)
            raise
        accepted = manager.acknowledge(plan, succeeded=True, lead_notes=result.lead_notes,
                                       generated_motif=result.generated_motif, **bounds)
        if not accepted:
            raise PlanError("execution receipt was not accepted; plan already reached a terminal status")
        return result
