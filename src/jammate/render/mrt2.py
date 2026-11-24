"""Official Magenta RT2 Python inference, with Standard MIDI -> 25 Hz tokens.

No JamMate state, InputEvent or custom wire protocol is required here.
The token meanings come from magenta_rt.config (upstream 2.0.3).
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from importlib.util import find_spec
from pathlib import Path
from platform import machine, system
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

FRAME_RATE = 25
OFFICIAL_SOURCE = "https://github.com/magenta/magenta-realtime/tree/694a545e4ba0b88bf1150137b129582166d3e07f"


def _has_exported_mlx_model(model):
    from magenta_rt.paths import models_dir

    folder = models_dir() / model
    return ((folder / f"{model}.mlxfn").is_file()
            and (folder / f"{model}_state.safetensors").is_file())


def configured_backend(model):
    configured = os.environ.get("JAMMATE_MRT2_ENGINE")
    if configured is not None:
        return configured
    if system() == "Darwin" and machine() == "arm64" and find_spec("mlx") is not None:
        try:
            if _has_exported_mlx_model(model):
                return "mlx"
        except ImportError:
            pass
    return "jax"


@lru_cache(maxsize=1)
def load_system(backend, model):
    import magenta_rt

    if backend == "jax":
        cls = magenta_rt.MagentaRT2Jax
    elif _has_exported_mlx_model(model):
        cls = magenta_rt.MagentaRT2StdMlxfn
    else:
        cls = magenta_rt.MagentaRT2Mlx
    return cls(size=model, cfg_scales={"musiccoca": 3., "notes": 3., "drums": 1.})


def midi_conditioning(path, duration):
    """Read real SMF timing, including tempo changes, into official token values.

    MIDI velocity, programs, channels and separate voices cannot be represented
    by this pitch-union + binary-drum conditioning. Frames use floor(onset) and
    ceil(offset), ensuring sub-frame notes are not erased. This is approximate.
    """
    import pretty_midi

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    count = math.ceil(duration * FRAME_RATE)
    notes = np.zeros((count, 128), dtype=np.int32)
    drums = np.zeros((count, 1), dtype=np.int32)
    attacks = np.zeros_like(notes, dtype=bool)
    midi = pretty_midi.PrettyMIDI(str(path))
    for instrument in midi.instruments:
        for note in instrument.notes:
            if note.start < 0 or note.end > duration + 1e-5:
                raise ValueError("MIDI notes must fit the declared duration; no silent truncation")
            first = min(count - 1, math.floor(note.start * FRAME_RATE + 1e-9))
            stop = min(count, max(first + 1, math.ceil(note.end * FRAME_RATE - 1e-9)))
            if instrument.is_drum:
                drums[first:stop, 0] = 1
            else:
                notes[first:stop, note.pitch] = 1
                attacks[first, note.pitch] = True
    notes[attacks] = 2
    return notes, drums


def generate(prompt, *, output, duration, midi=None, conditions=None, backend="jax", model="mrt2_small", system=None):
    """Run the official model. `system` injection is for labelled unit tests only."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("a nonempty style prompt is required")
    if not math.isfinite(duration) or not 0 < duration <= 120:
        raise ValueError("duration must be within (0,120] seconds")
    if backend not in {"jax", "mlx"} or model not in {"mrt2_small", "mrt2_base"}:
        raise ValueError("unsupported MRT2 backend/model")
    count = math.ceil(duration * FRAME_RATE)
    if conditions is not None and midi is not None:
        raise ValueError("provide either direct conditions or MIDI")
    if conditions is None:
        tokens = midi_conditioning(midi, duration) if midi is not None else None
    else:
        notes, drums = map(np.asarray, conditions)
        if (notes.shape != (count, 128) or drums.shape != (count, 1)
                or not np.isin(notes, [0, 1, 2]).all() or not np.isin(drums, [0, 1]).all()):
            raise ValueError("invalid MRT2 pitch/drum frame conditions")
        tokens = notes.astype(np.int32, copy=False), drums.astype(np.int32, copy=False)
    start = time.perf_counter()
    injected = system is not None
    if system is None:
        try:
            system = load_system(backend, model)
        except ImportError:
            raise RuntimeError("Install magenta-rt 2.0.3 with its JAX/MLX dependencies in this server's Python environment") from None
    style = system.tokenize_style(system.embed_style(prompt, use_mapper=True))
    state, pieces = None, []
    # Each generate call holds its conditioning constant. Continue model state
    # across runs, splitting whenever the actual MIDI conditioning changes.
    first = 0
    while first < count:
        stop = first + 1
        while stop < count and (tokens is None or all(np.array_equal(t[first], t[stop]) for t in tokens)):
            stop += 1
        conditioning = {"mulan_tokens_25hz": list(map(int, style))}
        if tokens is not None:
            conditioning.update(pianoroll_with_onsets_tokens=tokens[0][first].tolist(),
                                drum_pianoroll_tokens=tokens[1][first].tolist())
        wav, state = system.generate(conditioning=conditioning, frames=stop-first, state=state)
        if wav.sample_rate != 48000:
            raise ValueError("official MRT2 output must be 48 kHz")
        pieces.append(np.asarray(wav.samples))
        first = stop
    audio = np.concatenate(pieces, axis=0)[:round(duration * 48000)]
    if audio.shape != (round(duration * 48000), 2) or not np.isfinite(audio).all():
        raise ValueError("invalid MRT2 stereo output")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, 48000, subtype="PCM_16")
    return {"inference": "injected_test_double" if injected else "real_magenta_rt",
            "backend": backend, "model": model, "prompt": prompt, "frames": count,
            "duration_s": len(audio) / 48000, "elapsed_s": time.perf_counter()-start,
            "midi_sha256": hashlib.sha256(Path(midi).read_bytes()).hexdigest() if midi else None,
            "conditioning": ("text_and_direct_frames" if conditions is not None
                             else "text_and_midi" if midi is not None else "text_only"),
            "frame_quantization_s": 1/FRAME_RATE, "official_source": OFFICIAL_SOURCE}


def generate_isolated(prompt, *, output, duration, midi=None, conditions=None, backend="jax", model="mrt2_small"):
    """Keep model initialization and inference off the live playback server."""
    output = Path(output)
    command = [sys.executable, "-m", "jammate.render.mrt2", "--prompt", prompt,
               "--duration", str(duration), "--backend", backend, "--model", model, "--out", str(output)]
    if midi is not None:
        command.extend(("--midi", str(midi)))
    if conditions is not None:
        path = output.with_suffix(".conditions.npz")
        np.savez(path, notes=conditions[0], drums=conditions[1])
        command.extend(("--conditions", str(path)))
    try:
        process = subprocess.run(command, capture_output=True, timeout=150)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MRT2 inference timed out") from exc
    if process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace")
        detail = ""
        if ("FileNotFoundError:" in stderr and "magenta-rt-v2" in stderr
                or "[import_function] Failed to open" in stderr):
            detail = " 缺少模型文件或资源，请检查 MAGENTA_HOME 是否指向已下载的模型缓存。"
        elif "No Metal device available" in stderr or "[metal::load_device]" in stderr:
            detail = " 这个服务进程无法访问 Metal GPU，请在本机终端启动工作台。"
        elif "Install magenta-rt 2.0.3" in stderr or "ModuleNotFoundError:" in stderr:
            detail = " 当前工作台 Python 环境缺少模型依赖。"
        raise RuntimeError(
            f"MRT2 inference process failed ({backend}/{model}, exit {process.returncode}).{detail}"
        )
    return json.loads(output.with_suffix(".inference.json").read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--midi", type=Path)
    inputs.add_argument("--conditions", type=Path)
    parser.add_argument("--duration", type=float, default=4.)
    parser.add_argument("--backend", choices=("jax", "mlx"), default="jax")
    parser.add_argument("--model", choices=("mrt2_small", "mrt2_base"), default="mrt2_small")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    conditions = None
    if args.conditions:
        with np.load(args.conditions, allow_pickle=False) as data:
            conditions = (data["notes"], data["drums"])
    report = generate(args.prompt, output=args.out, duration=args.duration, midi=args.midi,
                      conditions=conditions, backend=args.backend, model=args.model)
    args.out.with_suffix(".inference.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
