"""Offline rhythm-to-accompaniment path, independent of pitch transcription."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from jammate.generate.arranger import arrange_bar
from jammate.director.policy import PolicyConfig
from jammate.music import Note
from jammate.render.synth import SR, render
from jammate.rhythm import RhythmContext, RhythmMode
from jammate.state import JamState


@dataclass(frozen=True)
class RhythmResult:
    context: RhythmContext
    mode: RhythmMode
    notes: tuple[Note, ...]
    audio: np.ndarray
    sr: int


def jam_rhythm(context: RhythmContext, bars: int = 8,
               mode: RhythmMode = RhythmMode.FOLLOW, sr: int = SR,
               energy: int = 2, start_s: float = 0.0) -> RhythmResult:
    """Render a repeated rhythm as a band. start_s is on the output clock.

    The input origin is retained for provenance; it does not add unintended
    silence to a standalone generated clip. Pass start_s explicitly to align
    the output with an input recording.
    """
    if not isinstance(bars, int) or not 1 <= bars <= 256:
        raise ValueError("bars must be within 1-256")
    if not math.isfinite(start_s) or start_s < 0 or not isinstance(sr, int) or sr <= 0:
        raise ValueError("start_s must be finite and nonnegative; sr must be a positive integer")
    if not context.onset_beats or context.confidence < PolicyConfig().min_rhythm_confidence:
        raise ValueError("rhythm is empty or uncertain; record another bar")
    state = JamState(tempo_bpm=context.tempo_bpm, energy=energy,
                     rhythm=context, rhythm_mode=mode)
    notes = tuple(n for bar in range(bars) for n in arrange_bar(state, bar))
    # Explicit length retains the final bar's trailing rests plus synth release.
    total_s = start_s + bars * 4 * 60.0 / context.tempo_bpm + 1.5
    audio = render(notes, context.tempo_bpm, sr, offset_s=start_s, total_s=total_s)
    return RhythmResult(context, state.rhythm_mode, notes, audio, sr)
