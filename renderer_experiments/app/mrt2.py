"""BandPlan adapter using the official Magenta RT2 model, not a fake audio proxy."""
from pathlib import Path

import numpy as np
import soundfile as sf

from jammate.render.capabilities import BackendCapabilityError, capability_report
from jammate.render.mrt2 import generate
from .band_plan import KevinRuntime


class MRT2Runtime(KevinRuntime):
    """Share preparation, MIDI verification and receipts with FluidSynth.

    Non-silent generated audio is a preview: without an independent audio
    verifier, it must never commit the exact BandPlan effects into JamState.
    Strict mode rejects before any model loading; preview is explicit.
    """
    backend_name = "magenta_rt2"

    def __init__(self, output, *, prompt="a warm instrumental band", model="mrt2_small",
                 engine="jax", allow_preview=False, seed=3800, generator=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("a nonempty MRT2 style prompt is required")
        if model not in {"mrt2_small", "mrt2_base"} or engine not in {"jax", "mlx"}:
            raise ValueError("unsupported MRT2 model/engine")
        super().__init__(output, executable=Path("."), soundfont=Path("."), sample_rate=48000, seed=seed)
        self.prompt, self.model, self.engine = prompt, model, engine
        self.allow_preview, self.generator = allow_preview, generator or generate

    def capabilities(self, plan, performance):
        return capability_report(plan, generative=True, silent=not performance.notes)

    def validate(self, state, plan):
        performance = super().validate(state, plan)
        report = self.capabilities(plan, performance)
        if not report["state_commit_allowed"] and not self.allow_preview:
            raise BackendCapabilityError(report)
        return performance

    def render_audio(self, folder, performance):
        duration = performance.bars * 4 * 60 / performance.tempo_bpm
        if not performance.notes:
            sf.write(folder / "band.wav", np.zeros((round(duration*48000), 2)), 48000, subtype="PCM_16")
            return {"inference": "host_silence_no_model", "reason": "silent plan"}
        return self.generator(self.prompt, output=folder / "band.wav", duration=duration,
                              midi=folder / "band.mid", backend=self.engine, model=self.model)
