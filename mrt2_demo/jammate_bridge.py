"""Generator callback for JamMate's offline MRT2Runtime."""
import hashlib
import math
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np
import soundfile as sf

from jammate.render.mrt2 import FRAME_RATE, OFFICIAL_SOURCE, generate as generate_upstream, midi_conditioning
from jammate.render.mrt2_style import english_style

from .inference import (
    DRUM_KEY, PITCH_KEY, SAMPLE_RATE, STYLE_KEY, create_mlxfn_model, encode_style, generate_frame,
)

_inference_lock = Lock()


@lru_cache(maxsize=1)
def _load_mlxfn(model):
    return create_mlxfn_model(model)


def generate(prompt, *, output, duration, midi=None, conditions=None, backend='jax', model='mrt2_small'):
    """Write the requested clip and return metadata for the runtime receipt."""
    if backend == 'jax':
        with _inference_lock:
            return generate_upstream(prompt, output=output, duration=duration, midi=midi,
                                     conditions=conditions, backend=backend, model=model)
    if backend != 'mlx' or model not in {'mrt2_small', 'mrt2_base'}:
        raise ValueError('unsupported MRT2 backend/model')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('a nonempty style prompt is required')
    prompt = english_style(prompt)
    if not math.isfinite(duration) or not 0 < duration <= 120:
        raise ValueError('duration must be within (0,120] seconds')
    if midi is not None and conditions is not None:
        raise ValueError('provide either direct conditions or MIDI')

    count = math.ceil(duration * FRAME_RATE)
    source = 'text_only'
    midi_hash = None
    if midi is not None:
        conditions = midi_conditioning(midi, duration)
        midi_hash = hashlib.sha256(Path(midi).read_bytes()).hexdigest()
        source = 'text_and_midi'
    elif conditions is not None:
        conditions = tuple(map(np.asarray, conditions))
        notes, drums = conditions
        if (notes.shape != (count, 128) or drums.shape != (count, 1)
                or not np.isin(notes, (0, 1, 2)).all() or not np.isin(drums, (0, 1)).all()):
            raise ValueError('invalid MRT2 pitch/drum frame conditions')
        conditions = notes.astype(np.int32, copy=False), drums.astype(np.int32, copy=False)
        source = 'text_and_direct_frames'

    start = time.perf_counter()
    with _inference_lock:
        engine = _load_mlxfn(model)
        conditioning = {STYLE_KEY: encode_style(engine, prompt)}
        state, blocks = None, []
        for frame in range(count):
            if conditions is not None:
                conditioning[PITCH_KEY] = conditions[0][frame].tolist()
                conditioning[DRUM_KEY] = conditions[1][frame].tolist()
            block, state = generate_frame(engine, conditioning, state)
            blocks.append(block)

    samples = np.concatenate(blocks)[:round(duration * SAMPLE_RATE)]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, samples, SAMPLE_RATE, subtype='PCM_16')
    return {'inference': 'real_magenta_rt', 'implementation': 'mlxfn', 'backend': backend, 'model': model,
            'prompt': prompt, 'frames': count, 'duration_s': len(samples) / SAMPLE_RATE,
            'elapsed_s': time.perf_counter() - start, 'midi_sha256': midi_hash, 'conditioning': source,
            'frame_quantization_s': 1 / FRAME_RATE, 'official_source': OFFICIAL_SOURCE}
