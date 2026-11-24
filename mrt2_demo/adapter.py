"""Frame conditioning for the standalone experiments."""
import math

from jammate.contracts import BandPlan, PlanError
from jammate.music import BEATS_PER_BAR
from jammate.orchestration.offline import prepare_plan

from .inference import DRUM_KEY, PITCH_KEY

class MRT2Adapter:
    def __init__(self, state, plan: BandPlan, strict=False):
        data = plan.to_dict()
        if data['effective_at']['beat'] != 0:
            raise PlanError('baseline renderer only executes bar-boundary plans')
        performance = prepare_plan(state, plan)
        if strict and data['preserve']:
            raise PlanError('MRT2 cannot guarantee exact preservation; route to MIDI')
        self.tempo_bpm = performance.tempo_bpm
        bar_seconds = BEATS_PER_BAR * 60 / self.tempo_bpm
        self.start_s = performance.start_bar * bar_seconds
        self.end_s = self.start_s + performance.bars * bar_seconds
        self.notes = performance.notes
        self.warnings = [f'Approximate only: {constraint}' for constraint in data['preserve']]
        self.previous = set()

    @classmethod
    def from_notes(cls, notes, tempo_bpm, end_s):
        """Experiment-only path: explicit ground-truth note list, no BandPlan expansion."""
        obj = cls.__new__(cls)
        obj.tempo_bpm = float(tempo_bpm)
        obj.start_s, obj.end_s = 0.0, float(end_s)
        obj.notes = tuple(notes)
        obj.warnings = ['Experiment mode: explicit notes, plan validation bypassed']
        obj.previous = set()
        return obj

    def frame(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('invalid timestamp')
        beat = seconds * self.tempo_bpm / 60
        active = [n for n in self.notes if n.start <= beat < n.start+n.duration]
        if not self.start_s <= seconds < self.end_s:
            active = []
        pitches = {n.pitch for n in active if n.instrument != 'drums'}
        identities = {(n.pitch,n.start) for n in active if n.instrument != 'drums'}
        onsets = {pitch for pitch,start in identities - self.previous}
        roll = [2 if p in onsets else 1 if p in pitches else 0 for p in range(128)]
        self.previous = identities
        return {PITCH_KEY: roll, DRUM_KEY: [int(any(n.instrument == 'drums' for n in active))]}
