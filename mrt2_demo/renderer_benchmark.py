"""Controlled MRT2 renderer experiments. Run with python -m mrt2_demo.renderer_benchmark."""

import argparse
import hashlib
import json
import math
import platform
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import pretty_midi
import soundfile as sf

try:
    import resource
except ImportError:
    resource = None

from jammate.analysis.jam_capture import analyze_capture
from jammate.music import Note
from jammate.render.mrt2 import FRAME_RATE, midi_conditioning
from .analyze_audio import analyze
from .inference import DRUM_KEY, PITCH_KEY, SAMPLE_RATE, STYLE_KEY, create_mlxfn_model, encode_style, generate_frame

CASES = json.loads(Path(__file__).with_name("renderer_cases.json").read_text())
BPM = 96
BEAT_SECONDS = 60 / BPM
KEEP_STYLE = object()


def write_json(path, value):
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    pending.replace(path)


def midi_file(path, notes):
    midi = pretty_midi.PrettyMIDI(initial_tempo=BPM)
    track = pretty_midi.Instrument(24)
    track.notes = [pretty_midi.Note(note.velocity, note.pitch, note.start * BEAT_SECONDS,
                                    note.end * BEAT_SECONDS) for note in notes]
    midi.instruments.append(track)
    midi.write(str(path))


def make_fixtures(folder):
    folder.mkdir()
    rate = 16000
    for name, rows in CASES["motifs"].items():
        notes = [Note(pitch, start, length) for start, pitch, length in rows]
        midi_file(folder / f"motif-{name}.mid", notes)
        samples = np.zeros(round(4 * BEAT_SECONDS * rate))
        for note in notes:
            start, end = (round(beat * BEAT_SECONDS * rate) for beat in (note.start, note.end))
            time = np.arange(end - start) / rate
            phase = 2 * np.pi * pretty_midi.note_number_to_hz(note.pitch) * time
            envelope = np.minimum(1, np.minimum(time, time[-1] - time) / .018)
            samples[start:end] += .26 * (np.sin(phase) + .18 * np.sin(2 * phase)) * envelope
        sf.write(folder / f"motif-{name}.wav", samples, rate)
    noise = np.random.default_rng(42)
    claps = np.zeros(round(8 * BEAT_SECONDS * rate))
    for beat in (0, 1, 2, 3, 4, 5.5, 6, 7.5):
        start = round(beat * BEAT_SECONDS * rate)
        claps[start:start + 640] = .18 * noise.normal(size=640) * np.exp(-np.arange(640) / 130)
    sf.write(folder / "H07-synthetic-claps.wav", claps, rate)
    sf.write(folder / "H08-silence.wav", np.zeros(rate * 2), rate)
    sf.write(folder / "H08-noise.wav", noise.normal(0, .001, rate * 2), rate)


def transcribe(audio, output, trace=None):
    from jammate.contracts.adapters import to_legacy_phrase

    samples, rate = sf.read(audio, dtype="float32", always_2d=True)
    event, diagnostics = analyze_capture(samples.mean(axis=1), rate, mode="humming",
                                         session_id="renderer-benchmark", bpm=BPM)
    if trace is not None:
        write_json(trace, {"input_event": event.to_dict() if event else None, "diagnostics": diagnostics})
    if event is None:
        raise ValueError("the JamMate humming recognizer returned no melody")
    midi_file(output, to_legacy_phrase(event, tempo_bpm=BPM).notes)


def convert(source, target, *options):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("ffmpeg is required for the matched WAV/M4A comparison")
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-i", str(source), *options, str(target)],
                   check=True, capture_output=True, timeout=25)


def short_stats(values):
    return {f"p{p}_ms": round(float(np.percentile(values, p)) * 1000, 3) for p in (50, 95, 99)}


def snapshot_source():
    root = Path(__file__).resolve().parents[1]
    source_files = ("mrt2_demo/renderer_benchmark.py", "mrt2_demo/renderer_cases.json",
                    "mrt2_demo/inference.py", "mrt2_demo/plan_conditioning.py")
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in source_files}
    try:
        def git(*args):
            return subprocess.check_output(["git", "--no-optional-locks", *args], cwd=root,
                                           text=True, stderr=subprocess.PIPE, timeout=8).strip()
        head, dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain"))
    except (OSError, subprocess.SubprocessError):
        head = dirty = None
    return {"git_head": head, "git_dirty": dirty, "source_sha256": files}


class TrialRunner:
    def __init__(self, output, size, suite):
        self.output, self.size, self.suite = output, size, suite
        self.rows = []
        self.source = snapshot_source()
        start = perf_counter()
        self.model = create_mlxfn_model(size)
        self.cold_load_s = perf_counter() - start

    def run(self, identifier, prompt, seconds, *, audio=None, midi=None, frames=None, segment=None, updates=None):
        from magenta_rt.audio import Waveform

        target = self.output / identifier
        target.mkdir()
        timing, blocks, changes = [], [], []
        count = math.ceil(seconds * FRAME_RATE)
        row = {"id": identifier, "mode": "S1_independent_segments" if segment else
               "S2_continuous" if updates is not None else "S0_complete_clip",
               "prompt": prompt, "reference_audio": Path(audio).name if audio else None,
               "midi": Path(midi).name if midi else None, "duration_s": seconds,
               "listening": dict.fromkeys(("clarity", "naturalness", "style", "instruments", "motif", "continuity"))}
        try:
            cpu_started = process_time()
            started = perf_counter()
            style = encode_style(self.model, Waveform.from_file(str(audio)) if audio else prompt)
            row["style_encode_s"] = perf_counter() - started
            if midi is not None:
                frames = midi_conditioning(midi, seconds)
            if frames is not None:
                notes, drums = frames
                np.savez_compressed(target / "frames.npz", **{
                    name: grid for name, grid in (("notes", notes), ("drums", drums)) if grid is not None
                })
                row["cue_stats"] = {"active_pitch_cells": int(np.count_nonzero(notes)) if notes is not None else None,
                                    "drum_frames": int(np.count_nonzero(drums)) if drums is not None else None,
                                    "peak_pitches_per_frame": int(np.count_nonzero(notes, axis=1).max())
                                    if notes is not None else None}
            conditioning = {STYLE_KEY: style}
            write_json(target / "conditioning.json", {"style_source": "audio" if audio else "text", **conditioning})
            state = None
            for index in range(count):
                if segment and index % round(segment * FRAME_RATE) == 0:
                    state = None
                if updates and index in updates:
                    value = updates[index]
                    t0 = perf_counter() - started
                    if value is None:
                        changes.append({"frame": index, "action": "stop", "t0_s": t0})
                        break
                    if value is KEEP_STYLE:
                        changes.append({"frame": index, "action": "keep", "t0_s": t0})
                    else:
                        style = encode_style(self.model, Waveform.from_file(str(value)) if isinstance(value, Path) else value)
                        conditioning[STYLE_KEY] = style
                        changes.append({"frame": index, "style": value.name if isinstance(value, Path) else value,
                                        "tokens": style, "t0_s": t0, "t2_s": perf_counter() - started})
                if frames is not None:
                    if frames[0] is not None:
                        conditioning[PITCH_KEY] = frames[0][index].tolist()
                    if frames[1] is not None:
                        conditioning[DRUM_KEY] = frames[1][index].tolist()
                mark = perf_counter()
                block, state = generate_frame(self.model, conditioning, state)
                timing.append(perf_counter() - mark)
                if changes and changes[-1]["frame"] == index and "t2_s" in changes[-1]:
                    changes[-1]["t3_s"] = perf_counter() - started
                blocks.append(block)
            samples = np.concatenate(blocks)[:round(seconds * SAMPLE_RATE)]
            wav = target / "audio.wav"
            sf.write(wav, samples, SAMPLE_RATE, subtype="PCM_16")
            frame_stats = short_stats(timing)
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
            row.update(status="completed", audio_seconds=len(samples) / SAMPLE_RATE,
                       sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
                       first_pcm_s=row["style_encode_s"] + timing[0], wall_s=perf_counter() - started,
                       rtf=sum(timing) / (len(samples) / SAMPLE_RATE), frame_ms=frame_stats,
                       frame_deadline_misses=sum(elapsed > 1 / FRAME_RATE for elapsed in timing),
                       audio=analyze(samples, SAMPLE_RATE), updates=changes,
                       process_cpu_s=process_time() - cpu_started,
                       process_peak_rss_bytes=peak_rss * (1 if platform.system() == "Darwin" else 1024)
                       if peak_rss is not None else None)
            write_json(target / "frame_timings_ms.json", [round(value * 1000, 3) for value in timing])
        except (ValueError, RuntimeError, OSError) as error:
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(target / "result.json", row)
        self.rows.append(row)
        self.save()
        print(f"{identifier}: {row['status']} (RTF={row.get('rtf', 'n/a')})", flush=True)

    def skip(self, identifier, reason):
        self.rows.append({"id": identifier, "status": "not_run", "reason": reason})
        self.save()

    def save(self):
        write_json(self.output / "summary.json", {"platform": platform.platform(), "machine": platform.machine(),
                   "model": self.size, "backend": "MagentaRT2StdMlxfn", "package": version("magenta-rt"),
                   "mlx": version("mlx"), "sampler": "exported model defaults, no seed override",
                   **self.source,
                   "cold_load_s": self.cold_load_s, "suite": self.suite, "results": self.rows})


def audio_trials(runner, folder, seconds, human, corrected_midi, reference_midi):
    source = human or folder / "motif-B.wav"
    if not human:
        runner.skip("H01/H02", "No consented human reference was supplied; B uses the synthetic Motif B.")
    prompt = CASES["prompts"]["A01"]
    runner.run("B01", prompt, seconds)
    runner.run("B02", prompt, seconds, audio=source)
    recognized = folder / "recognized.mid"
    try:
        transcribe(source, recognized, folder / "recognized.json")
        runner.run("B03", prompt, seconds, midi=recognized)
    except (ValueError, OSError) as error:
        runner.skip("B03", str(error))
    if corrected_midi:
        runner.run("B04", prompt, seconds, midi=corrected_midi)
    else:
        runner.skip("B04", "No independently validated LLM correction or human transcript was supplied.")
    standard = reference_midi if human else folder / "motif-B.mid"
    if standard:
        runner.run("B05", prompt, seconds, midi=standard)
        runner.run("B06", prompt, seconds, audio=source, midi=standard)
    else:
        runner.skip("B05/B06", "A matching verified MIDI is required for the provided human WAV.")
    m4a = folder / "H03.m4a"
    decoded = folder / "H03-decoded.wav"
    try:
        convert(source, m4a, "-c:a", "aac", "-b:a", "96k")
        convert(m4a, decoded, "-ac", "1", "-ar", str(sf.info(source).samplerate), "-c:a", "pcm_s16le")
        write_json(folder / "H03-decode.json", {
            label: {"frames": sf.info(path).frames, "sample_rate": sf.info(path).samplerate,
                    "channels": sf.info(path).channels} for label, path in (("original", source), ("decoded_m4a", decoded))
        })
        runner.run("H03-M4A", prompt, seconds, audio=decoded)
    except (FileNotFoundError, subprocess.SubprocessError, RuntimeError) as error:
        runner.skip("H03-M4A", str(error))
    for fixture in ("H07-synthetic-claps", "H08-silence", "H08-noise"):
        runner.run(fixture, prompt, seconds, audio=folder / f"{fixture}.wav")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--groups", nargs="+", choices=("prompts", "audio", "modes", "references"),
                        default=["prompts", "audio", "modes"])
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--lengths", nargs="+", type=int, default=[10, 30, 60])
    parser.add_argument("--model", default="mrt2_small")
    parser.add_argument("--human", type=Path, help="Optional consented local WAV for direct/automatic conditioning")
    parser.add_argument("--corrected-midi", type=Path, help="Reviewed correction of the chosen source for B04")
    parser.add_argument("--reference-midi", type=Path, help="Verified ground truth matching --human for B05/B06")
    parser.add_argument("--bossa-wav", type=Path, help="WAV for H05")
    parser.add_argument("--rock-wav", type=Path, help="WAV for H06 and a continuous WAV update")
    args = parser.parse_args()
    if not 3 <= args.seconds <= 120 or not 1 <= args.repeats <= 10 or any(not 2 <= n <= 120 for n in args.lengths):
        parser.error("seconds must be 3–120, repeats 1–10, and lengths 2–120")
    args.out.mkdir(parents=True, exist_ok=False)
    folder = args.out / "fixtures"
    make_fixtures(folder)
    runner = TrialRunner(args.out, args.model, args.groups)
    if "prompts" in args.groups:
        for tag, prompt in CASES["prompts"].items():
            for repeat in range(1, args.repeats + 1):
                runner.run(f"{tag}-r{repeat}", prompt, args.seconds)
    if "audio" in args.groups:
        audio_trials(runner, folder, args.seconds, args.human, args.corrected_midi, args.reference_midi)
    if "modes" in args.groups:
        for tag in ("A01", "A05"):
            prompt = CASES["prompts"][tag]
            for seconds in args.lengths:
                runner.run(f"{tag}-S0-{seconds}", prompt, seconds)
            longest = max(args.lengths)
            runner.run(f"{tag}-S1-{longest}", prompt, longest, segment=10)
        prompt = CASES["prompts"]["A01"]
        runner.run("D-S2-bossa", prompt, 61, updates={
            250: prompt + ", more energetic, same groove",
            500: args.human or folder / "motif-A.wav",
            750: "bossa nova, nylon guitar, sparse percussion, space for melody",
            1000: prompt + ", D major", 1250: KEEP_STYLE, 1500: None,
        })
    if "references" in args.groups:
        for identifier, audio, prompt in (("H05", args.bossa_wav, "A01"), ("H06", args.rock_wav, "A05")):
            if audio:
                runner.run(identifier, CASES["prompts"][prompt], args.seconds, audio=audio)
            else:
                runner.skip(identifier, "No reference WAV supplied.")
        if args.rock_wav:
            runner.run("B-WAV-live-update", CASES["prompts"]["A01"], 20, updates={250: args.rock_wav})
        rock = CASES["prompts"]["A05"]
        runner.run("D-S2-rock", rock, 41, updates={250: folder / "motif-B.wav",
                   500: rock + ", harder drums", 750: rock, 1000: None})
    runner.save()
    if any(row["status"] == "failed" for row in runner.rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
