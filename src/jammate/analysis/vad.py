"""Block-level phrase detector for the microphone stream.

A phrase starts at the first loud block and ends after `end_silence_blocks`
quiet blocks; phrases shorter than `min_phrase_blocks` (coughs, taps) are
dropped, and sound that never stops (band bleed, a fan) is cut every
`max_phrase_blocks` so memory stays bounded. Pure state-transition function
so it can be unit tested.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class VadConfig:
    threshold_db: float = -40.0
    min_phrase_blocks: int = 8  # ~0.4 s with 1024-sample blocks at 22.05 kHz
    end_silence_blocks: int = 13  # ~0.6 s of quiet ends the phrase
    max_phrase_blocks: int = 323  # ~15 s: longer sound is cut into pieces


@dataclass(frozen=True)
class VadState:
    in_phrase: bool = False
    start: int = 0
    last_loud: int = -1
    block: int = 0  # index of the next block to be processed


def level_db(block: np.ndarray) -> float:
    rms = math.sqrt(float(np.mean(np.square(block, dtype=np.float64)))) if block.size else 0.0
    return 20.0 * math.log10(max(rms, 1e-12))


def vad_step(state: VadState, level: float,
             config: VadConfig) -> tuple[VadState, tuple[int, int] | None]:
    """Feed one block level; returns (new state, (start, end) blocks of a finished phrase)."""
    index = state.block
    if level >= config.threshold_db:
        if not state.in_phrase:
            return VadState(True, index, index, index + 1), None
        if index + 1 - state.start >= config.max_phrase_blocks:
            return VadState(block=index + 1), (state.start, index + 1)
        return replace(state, last_loud=index, block=index + 1), None
    if state.in_phrase and index - state.last_loud >= config.end_silence_blocks:
        end = state.last_loud + 1
        phrase = (state.start, end) if end - state.start >= config.min_phrase_blocks else None
        return VadState(block=index + 1), phrase
    return replace(state, block=index + 1), None
