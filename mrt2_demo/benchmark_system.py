"""Run normal JamMate HTTP inputs, save receipts and compare actual MRT2 conditions."""

import argparse
import base64
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from jammate.generate.arranger import chord_for_bar
from jammate.music import BEATS_PER_BAR, Note
from jammate.orchestration.offline import prepare_bar
from jammate.render.midi_out import write_midi
from jammate.state import JamState
from .renderer_benchmark import BPM, CASES, TrialRunner, convert, make_fixtures, write_json


def recording(path):
    return {"mime_type": "audio/wav", "audio": base64.b64encode(path.read_bytes()).decode()}


class Jam:
    def __init__(self, url, folder, renderer, agent, opening, mode="cold", *, audio_reference=False):
        self.url, self.folder = url, folder
        self.folder.mkdir(parents=True)
        self.requests = []
        self.view = self.post("start", opening=opening, opening_mode=mode, agent=agent, renderer=renderer,
                              allow_preview=renderer == "mrt2", mrt2_audio_reference=audio_reference)
        self.session = self.view["session"]
        self.packet = self.view["next"]
        self.pcm = []
        self.mrt = renderer in {"mrt2_direct", "mrt2_stream"}
        self.playback = []

    def post(self, operation, **fields):
        start = time.perf_counter()
        request = urllib.request.Request(self.url + "/api/jam/" + operation, data=json.dumps(fields).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.load(response)
            return result
        except urllib.error.HTTPError as error:
            raise ValueError(f"{operation}: {json.load(error).get('error')}") from None
        finally:
            self.requests.append({"operation": operation, "ms": (time.perf_counter() - start) * 1000})

    def poll(self, packet, sample=None):
        return self.post("poll", session=self.session, started=packet["bar"], buffered=packet["bar"],
                         sample=packet["start"] if sample is None else sample)

    def play(self):
        packet = self.packet
        bar = packet["bar"]
        if self.mrt:
            query = urllib.parse.urlencode({"session": self.session, "bar": bar})
            start = time.perf_counter()
            with urllib.request.urlopen(self.url + "/api/jam/mrt2-stream?" + query, timeout=90) as response:
                first = response.read(4)
                first_pcm_ms = (time.perf_counter() - start) * 1000
                pcm = first + response.read()
            if len(pcm) != packet["pcm_frames"] * 4:
                raise ValueError("incomplete MRT2 PCM")
            self.pcm.append(pcm)
            self.playback.append({"bar": bar, "first_pcm_ms": first_pcm_ms, "pcm_bytes": len(pcm)})
        else:
            (self.folder / f"bar-{bar:03d}.wav").write_bytes(base64.b64decode(packet["wav"], validate=True))
        self.view = self.poll(packet)
        return self.view

    def advance(self):
        self.packet = self.poll(self.packet, self.packet["end"] - 1)["next"]
        return self.play()

    def debug(self):
        return self.post("debug", session=self.session)

    def turn(self, identifier, texts):
        start = time.perf_counter()
        before = self.debug()["jam_state"]
        events = []
        for item in texts:
            data = {"text": item} if isinstance(item, str) else {"event": item}
            events.append(self.post("feedback", session=self.session, **data)["feedback"]["id"])
        feedback = {}
        for _ in range(180):
            feedback = self.poll(self.packet)["feedback"]
            if feedback["status"] in {"scheduled", "held", "expired", "applied"}:
                break
            if feedback["status"] == "buffered":
                self.advance()
            else:
                time.sleep(.1)
        if feedback.get("status") == "scheduled":
            for _ in range(12):
                feedback = self.advance()["feedback"]
                if feedback["status"] == "applied":
                    break
        debug = self.debug()
        result = {"id": identifier, "input": texts, "event_ids": events, "before": before,
                  "after": debug["jam_state"], "feedback": feedback,
                  "turns": [row for row in debug["turns"] if row["input_event"]["event_id"] in events],
                  "stream": debug.get("mrt2_stream"), "elapsed_ms": (time.perf_counter() - start) * 1000,
                  "first_audible_s": None, "perceived_change_s": None}
        write_json(self.folder / f"{identifier}.json", result)
        return {"id": identifier, "status": feedback.get("status"), "message": feedback.get("message"),
                "playback": feedback.get("playback")}

    def close(self):
        if self.pcm:
            with wave.open(str(self.folder / "stream.wav"), "wb") as output:
                output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                output.writeframes(b"".join(self.pcm))
        write_json(self.folder / "http.json", {"requests": self.requests, "playback": self.playback,
                                               "clock": "scripted playback acknowledgements"})
        self.post("stop", session=self.session)


def run_opening(url, folder, renderer, agent, opening, mode="cold"):
    jam = Jam(url, folder, renderer, agent, opening, mode)
    try:
        jam.play()
        report = jam.debug()
        write_json(folder / "opening.json", report)
        return report
    finally:
        jam.close()


def system_runs(args, fixtures):
    rows = []
    for case in ("E01", "E02", "E03", "E04", "E05"):
        opening = {"text": CASES["system_turns"][case]}
        if case == "E04":
            opening["humming"] = recording(fixtures / "motif-A.wav")
        if case == "E05":
            opening["rhythm"] = recording(fixtures / "H07-synthetic-claps.wav")
        try:
            view = run_opening(args.url, args.out / case, args.renderer, args.agent, opening,
                               "humming" if case == "E04" else "cold")
            rows.append({"id": case, "status": "observed", "bpm": view["bpm"], "key": view["key"]})
        except (ValueError, OSError) as error:
            rows.append({"id": case, "status": "failed", "error": str(error)})
    if args.openings_only:
        return rows
    opening = {"text": "C 大调，和弦设为 C-Am-F-G，第 1、3 拍。bossa nova, nylon guitar, light percussion",
               "humming": recording(fixtures / "motif-A.wav")}
    jam = Jam(args.url, args.out / "continuous", args.renderer, args.agent, opening)
    try:
        jam.play()
        for label, text in (("add-guitar", "加入吉他"), ("add-strings", "加入弦乐")):
            rows.append(jam.turn(label, [text]))
        for identifier, text in CASES["system_turns"].items():
            if int(identifier[1:]) >= 6:
                rows.append(jam.turn(identifier, [text] if isinstance(text, str) else text))
        write_json(jam.folder / "final.json", jam.debug())
    finally:
        jam.close()
    return rows


def multimodal_run(args, fixtures):
    opening = {"text": "C 大调，和弦设为 C-Am-F-G，第 1、3 拍"}
    jam = Jam(args.url, args.out / "live-inputs", args.renderer, args.agent, opening)
    outcomes = []
    try:
        jam.play()
        for index, (mode, name) in enumerate((
            ("humming", "motif-A.wav"), ("rhythm", "H07-synthetic-claps.wav"),
            ("humming", "motif-B.wav"), ("humming", "H08-silence.wav"),
            ("humming", "H08-noise.wav"),
        )):
            capture = jam.post("capture", session=jam.session, mode=mode, **recording(fixtures / name))
            outcomes.append({"id": name, "capture": capture})
            if capture.get("event"):
                outcomes.append(jam.turn(f"capture-{index}", [capture["event"]]))
            if index < 3:
                outcomes.append(jam.turn(f"revise-{index}", ["鼓再强一点", "等一下，保持原样"]))
        outcomes.append(jam.turn("stop", ["停止演奏"]))
    finally:
        jam.close()
    return outcomes


def ablation(args, runner):
    from jammate.render.mrt2 import midi_conditioning

    first = {"bossa-zh": "A01", "bossa-en": "A01", "rock-zh": "A05", "rock-en": "A05"}
    for label, text in CASES["system_inputs"].items():
        report = run_opening(args.url, args.out / label, "mrt2_direct", "rule", {"text": text, "key": "C 大调"})
        actual = report["mrt2_stream"]["recent_bars"][0]["style_prompt"]
        baseline = CASES["prompts"][first[label]]
        state = JamState()
        bars = int(args.seconds * BPM / (60 * BEATS_PER_BAR))
        chords = tuple(Note(pitch, i * BEATS_PER_BAR, 1.5, instrument="keys")
                       for i in range(bars) for pitch in chord_for_bar(state, i).voicing(52))
        melody = tuple(Note(pitch, beat, length, instrument="lead") for beat, pitch, length in CASES["motifs"]["A"])
        full = tuple(note for i in range(bars) for note in prepare_bar(state, i))
        drums = tuple(note for note in full if note.instrument == "drums" and note.pitch in {36, 38})
        runner.run(label + "-A", baseline, args.seconds)
        runner.run(label + "-B", actual, args.seconds)
        for group, notes in (("C", chords), ("D", chords + melody + drums), ("E", full + melody)):
            path = write_midi(notes, BPM, args.out / f"{label}-{group}.mid")
            runner.run(f"{label}-{group}", actual, args.seconds, frames=midi_conditioning(path, args.seconds))


def audit(args, fixtures, runner):
    import numpy as np
    import soundfile as sf
    from jammate.render.mrt2 import FRAME_RATE, midi_conditioning
    from .jammate_bridge import generate

    prompt = CASES["prompts"]["A01"]
    seconds = 5
    runner.run("Q01", prompt, seconds)
    folder = args.out / "Q02"
    folder.mkdir()
    receipt = generate(prompt, duration=seconds, output=folder / "audio.wav", backend="mlx")
    receipt["sha256"] = hashlib.sha256((folder / "audio.wav").read_bytes()).hexdigest()
    write_json(folder / "result.json", receipt)

    state = JamState()
    solo = tuple(Note(pitch, beat, length, instrument="lead") for beat, pitch, length in CASES["motifs"]["A"])
    full = tuple(note for bar in range(2) for note in prepare_bar(state, bar))
    sparse = midi_conditioning(write_midi(solo, BPM, fixtures / "q04.mid"), seconds)
    complete = midi_conditioning(write_midi(full + solo, BPM, fixtures / "q05.mid"), seconds)
    reference = args.reference
    if reference:
        audio, rate = sf.read(reference, always_2d=True)
        write_json(fixtures / "reference.json", {"file": reference.name, "seconds": len(audio) / rate,
                   "sample_rate": rate, "channels": audio.shape[1], "encoding": sf.info(reference).subtype,
                   "rms": float(np.sqrt(np.mean(audio ** 2)))})
        runner.run("Q06", prompt, seconds, audio=reference)
        aac = fixtures / "reference.m4a"
        decoded = fixtures / "reference-m4a-decoded.wav"
        try:
            convert(reference, aac, "-c:a", "aac", "-b:a", "128k")
            convert(aac, decoded, "-ar", str(rate), "-c:a", "pcm_s16le")
            runner.run("H01", prompt, 12, updates={8 * FRAME_RATE: reference})
            runner.run("H02", prompt, 12, updates={8 * FRAME_RATE: decoded})
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            runner.skip("H02", str(error))
    else:
        runner.skip("Q06/H01/H02", "No WAV reference was supplied.")

    jam = Jam(args.url, args.out / "Q07", "mrt2_direct", "rule", {"text": prompt}, "warm")
    try:
        jam.play()
        jam.advance()
        debug = jam.debug()
        write_json(jam.folder / "opening.json", debug)
    finally:
        jam.close()
    actual = debug["mrt2_stream"]["recent_bars"][0]["style_prompt"]
    runner.run("Q03", actual, seconds)
    runner.run("Q04", actual, seconds, frames=(sparse[0], None))
    runner.run("Q04-with-zero-drums", actual, seconds, frames=sparse)
    runner.run("Q03-with-zero-grids", actual, seconds, frames=tuple(np.zeros_like(grid) for grid in sparse))
    runner.run("Q05", actual, seconds, frames=complete)

    jam = Jam(args.url, args.out / "H-integrated", "mrt2_direct", "rule",
              {"text": prompt, "humming": recording(fixtures / "motif-A.wav")}, audio_reference=True)
    try:
        jam.play()
        jam.advance()
        write_json(jam.folder / "opening.json", jam.debug())
    finally:
        jam.close()

    opening = {"text": "C 大调，和弦设为 C-Am-F-G，第 1、3 拍。" + prompt}
    jam = Jam(args.url, args.out / "Q08", "mrt2", "rule", opening)
    try:
        jam.play()
        for _ in range(200):
            view = jam.poll(jam.packet)
            status = (view["opening_preview"] or {}).get("status")
            if view["preview_id"]:
                preview = jam.post("preview", session=jam.session, id=view["preview_id"])
                (jam.folder / "preview.wav").write_bytes(base64.b64decode(preview["wav"], validate=True))
                break
            if status in {"failed", "waiting_material"}:
                break
            time.sleep(.25)
        write_json(jam.folder / "opening.json", jam.debug())
    finally:
        jam.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:4198")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--renderer", default="synth")
    parser.add_argument("--agent", choices=("rule", "llm"), default="rule")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ablation", action="store_true")
    mode.add_argument("--multimodal", action="store_true")
    mode.add_argument("--openings-only", action="store_true")
    mode.add_argument("--audit", action="store_true")
    parser.add_argument("--reference", type=Path, help="Local WAV for the optional audit audio trials")
    parser.add_argument("--seconds", type=int, default=10)
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.path:
        parser.error("use a local JamMate origin")
    if not 5 <= args.seconds <= 120:
        parser.error("seconds must be 5–120")
    args.out.mkdir(parents=True, exist_ok=False)
    fixtures = args.out / "fixtures"
    make_fixtures(fixtures)
    if args.audit:
        audit(args, fixtures, TrialRunner(args.out, "mrt2_small", "harness_audit"))
    elif args.ablation:
        ablation(args, TrialRunner(args.out, "mrt2_small", "A3"))
    else:
        rows = multimodal_run(args, fixtures) if args.multimodal else system_runs(args, fixtures)
        write_json(args.out / "summary.json", rows)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
