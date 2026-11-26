"""BandPlan adapter using the official Magenta RT2 model, not a fake audio proxy."""
from pathlib import Path
import math

import numpy as np
import soundfile as sf

from jammate.orchestration.backend_arrangement import audible_layers
from jammate.orchestration.offline import PlannedPerformance
from jammate.render.capabilities import BackendCapabilityError, capability_report
from jammate.render.mrt2 import FRAME_RATE, generate
from .band_plan import KevinRuntime, _write


class MRT2Runtime(KevinRuntime):
    """Share preparation, MIDI verification and receipts with FluidSynth.

    Non-silent generated audio is a preview: without an independent audio
    verifier, it must never commit the exact BandPlan effects into JamState.
    Strict mode rejects before any model loading; preview is explicit.
    """
    backend_name = "magenta_rt2"

    def __init__(self, output, *, prompt="a warm instrumental band", model="mrt2_small",
                 engine="jax", allow_preview=False, seed=3800, generator=None, arranger=None, backend_arranger=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("a nonempty MRT2 style prompt is required")
        if model not in {"mrt2_small", "mrt2_base"} or engine not in {"jax", "mlx"}:
            raise ValueError("unsupported MRT2 model/engine")
        super().__init__(output, executable=Path("."), soundfont=Path("."), sample_rate=48000, seed=seed,
                         arranger=arranger, backend_arranger=backend_arranger)
        self.prompt, self.model, self.engine = prompt, model, engine
        self.allow_preview, self.generator = allow_preview, generator or generate

    def capabilities(self, plan, performance, conditioning=None):
        return capability_report(plan, generative=True, silent=not (performance.notes or conditioning is not None))

    def validate(self, state, plan):
        prepared = super().validate(state, plan)
        if self.backend_arranger is None:
            report = self.capabilities(plan, prepared)
        else:
            report = capability_report(plan, generative=True, silent=not audible_layers(prepared, plan))
        if not report["state_commit_allowed"] and not self.allow_preview:
            raise BackendCapabilityError(report)
        return prepared

    def prepare_execution(self, state, plan, events, check_pending):
        if self.backend_arranger is None:
            return super().prepare_execution(state, plan, events, check_pending)
        target = self.validate(state, plan)
        data = plan.to_dict()
        performance = PlannedPerformance((), (), None, target.tempo_bpm,
                                         data["effective_at"]["bar"], data["valid_for_bars"], beats_per_bar=target.beats_per_bar)
        if not audible_layers(target, plan):
            self.backend_arranger.skip(plan, "host_silence")
            return performance, None
        duration = performance.bars * performance.beats_per_bar * 60 / performance.tempo_bpm
        conditioning = self.backend_arranger.mrt(events, state, plan, target, duration, self.prompt)
        check_pending()
        return performance, conditioning

    def export_audio(self, folder, performance, conditioning):
        if self.backend_arranger is None:
            return super().export_audio(folder, performance, conditioning)
        if conditioning is None:
            return self.render_audio(folder, performance)
        _write(folder / "conditioning.json", conditioning.to_dict())
        duration = performance.bars * performance.beats_per_bar * 60 / performance.tempo_bpm
        return self.generator(conditioning.style, output=folder / "band.wav", duration=duration,
                              conditions=conditioning.tokens(math.ceil(duration * FRAME_RATE)),
                              backend=self.engine, model=self.model)

    def render_audio(self, folder, performance):
        duration = performance.bars * 4 * 60 / performance.tempo_bpm
        if not performance.notes:
            sf.write(folder / "band.wav", np.zeros((round(duration*48000), 2)), 48000, subtype="PCM_16")
            return {"inference": "host_silence_no_model", "reason": "silent plan"}
        return self.generator(self.prompt, output=folder / "band.wav", duration=duration,
                              midi=folder / "band.mid", backend=self.engine, model=self.model)
