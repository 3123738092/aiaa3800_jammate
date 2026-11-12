"""Backend selection is host configuration, independent of rule/LLM planning."""
from pathlib import Path


def create_runtime(output, *, backend="default", fluidsynth=None, soundfont=None,
                   profile="electric", allow_preview=False):
    if backend == "mrt2":
        import os
        from renderer_experiments.app.mrt2 import MRT2Runtime
        return MRT2Runtime(Path(output), allow_preview=allow_preview,
                           engine=os.environ.get("JAMMATE_MRT2_ENGINE", "jax"),
                           model=os.environ.get("JAMMATE_MRT2_MODEL", "mrt2_small"),
                           prompt=os.environ.get("JAMMATE_MRT2_PROMPT", "a warm instrumental band"))
    if backend not in {"default", "fluidsynth"}:
        raise ValueError("unknown audio backend")
    if backend == "fluidsynth" and (not fluidsynth or not soundfont):
        raise ValueError("FluidSynth executable and SoundFont are required")
    if fluidsynth:
        from renderer_experiments.app.band_plan import KevinRuntime
        return KevinRuntime(Path(output), executable=Path(fluidsynth), soundfont=Path(soundfont), profile=profile)
    from jammate.orchestration.offline import OfflineRuntime
    return OfflineRuntime()
