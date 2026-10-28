"""Offline jam: recorded count-in + hummed phrase in, band answer out.

Same director and arranger as live mode, but over a recording, so a session
is reproducible (fixed seed) and can be evaluated without a microphone.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from jammate.analysis.pitch import segment_notes, to_beats, track_pitch
from jammate.analysis.tempo import detect_onsets, tempo_from_onsets
from jammate.director.policy import Decision, PolicyConfig, Silence, UserPhrase, decide
from jammate.generate.arranger import arrange_bar, place_lead
from jammate.music import BEATS_PER_BAR, Note
from jammate.render.synth import mix, render
from jammate.state import MAX_TEMPO_BPM, MIN_TEMPO_BPM, JamState

COUNT_IN_GUARD_S = 0.1  # onsets this close to the first sung note belong to the hum
EARLY_START_BEATS = 0.125  # singing up to a 32nd early still counts as "on the beat"


@dataclass(frozen=True)
class PhraseAnalysis:
    tempo_bpm: float
    tempo_source: str  # count-in | hint | default
    notes: tuple[Note, ...]  # beats; beat 0 = first downbeat of the phrase
    origin_s: float  # time of beat 0 in the input audio
    count_in_onsets: tuple[float, ...]


@dataclass(frozen=True)
class JamResult:
    analysis: PhraseAnalysis
    decisions: tuple[Decision, ...]
    notes: tuple[Note, ...]  # band part, beats from `analysis.origin_s`
    band_audio: np.ndarray
    mix_audio: np.ndarray
    sr: int


def _beat_origin(first_voiced: float | None, count_in: np.ndarray, tempo: float,
                 from_count_in: bool) -> float:
    if not from_count_in:
        return first_voiced if first_voiced is not None else 0.0
    period = 60.0 / tempo
    if first_voiced is None:
        return float(count_in[-1] + period)
    beats_after_first_clap = (first_voiced - count_in[0]) / period + EARLY_START_BEATS
    return float(count_in[0] + math.floor(beats_after_first_clap) * period)


def analyze_phrase(y: np.ndarray, sr: int, tempo_hint: float | None = None,
                   default_tempo: float = 96.0) -> PhraseAnalysis:
    times, midi, voiced = track_pitch(y, sr)
    timed = segment_notes(times, midi, voiced)
    first_voiced = timed[0].start_s if timed else None
    onsets = detect_onsets(y, sr)
    count_in = onsets if first_voiced is None else onsets[onsets < first_voiced - COUNT_IN_GUARD_S]
    tempo = tempo_from_onsets(count_in, MIN_TEMPO_BPM, MAX_TEMPO_BPM)
    source = "count-in"
    if tempo is None:
        tempo, source = (tempo_hint, "hint") if tempo_hint else (default_tempo, "default")
        # Quantize at the tempo the band will actually play, or hum and band drift apart.
        tempo = min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, tempo))
    origin = _beat_origin(first_voiced, count_in, tempo, source == "count-in")
    notes = to_beats(timed, tempo, origin) if timed else ()
    return PhraseAnalysis(tempo, source, notes, origin, tuple(float(t) for t in count_in))


def _bars_spanned(notes: tuple[Note, ...]) -> int:
    return math.ceil(max(n.end for n in notes) / BEATS_PER_BAR) if notes else 0


def plan_jam(analysis: PhraseAnalysis, bars: int, rng: random.Random,
             initial: JamState = JamState(),
             config: PolicyConfig = PolicyConfig()) -> tuple[tuple[Decision, ...], tuple[Note, ...]]:
    """Band notes for `bars` bars: keep time under the phrase, answer it, then invite."""
    before = initial.with_tempo(analysis.tempo_bpm)
    phrase_bars = _bars_spanned(analysis.notes)
    first = decide(before, UserPhrase(analysis.notes), rng, config)
    decisions, current = [first], first.state
    leads = place_lead(first.lead, start_bar=phrase_bars)
    notes: list[Note] = []
    quiet_bars = 0
    for bar in range(bars):
        if bar < phrase_bars:
            # The band has not heard the phrase yet: keep time with drums only.
            notes += [n for n in arrange_bar(before, bar) if n.instrument == "drums"]
            continue
        lead_pending = bar <= max(leads, default=-1)
        if not lead_pending and quiet_bars >= config.initiate_after_bars:
            decision = decide(current, Silence(quiet_bars), rng, config)
            decisions.append(decision)
            current = decision.state
            leads = {**leads, **place_lead(decision.lead, start_bar=bar)}
        notes += arrange_bar(current, bar, leads.get(bar, ()))
        quiet_bars = 0 if bar in leads else quiet_bars + 1
    return tuple(decisions), tuple(notes)


def jam_offline(y: np.ndarray, sr: int, bars: int = 8, seed: int = 0,
                tempo_hint: float | None = None, initial: JamState = JamState()) -> JamResult:
    """Analyse a recording and render the band's answer (constant tempo)."""
    analysis = analyze_phrase(y, sr, tempo_hint=tempo_hint, default_tempo=initial.tempo_bpm)
    decisions, notes = plan_jam(analysis, bars, random.Random(seed), initial)
    tempo = decisions[0].state.tempo_bpm
    band = render(notes, tempo, sr=sr, offset_s=analysis.origin_s)
    return JamResult(analysis, decisions, notes, band,
                     mix(np.asarray(y, dtype=np.float32), band), sr)
