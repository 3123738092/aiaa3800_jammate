"""Shared MRT2 frame operations for live playback and offline export."""
import numpy as np

from jammate.render.mrt2 import FRAME_RATE

SAMPLE_RATE = 48_000
FRAME_SAMPLES = SAMPLE_RATE // FRAME_RATE
STYLE_KEY = 'mulan_tokens_25hz'
PITCH_KEY = 'pianoroll_with_onsets_tokens'
DRUM_KEY = 'drum_pianoroll_tokens'


def encode_style(model, value):
    embedding = model.embed_style(value, use_mapper=isinstance(value, str))
    return [int(token) for token in model.tokenize_style(embedding)]


def generate_frame(model, conditioning, state):
    waveform, state = model.generate(conditioning=conditioning, frames=1, state=state)
    samples = np.asarray(waveform.samples)
    if (waveform.sample_rate != SAMPLE_RATE or samples.shape != (FRAME_SAMPLES, 2)
            or not np.isfinite(samples).all()):
        raise ValueError('MRT2 returned an invalid 48 kHz stereo frame')
    return samples, state
