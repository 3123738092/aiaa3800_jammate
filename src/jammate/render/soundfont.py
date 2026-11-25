"""Discover and render the actual bank-zero timbres of a configured SoundFont."""
from functools import lru_cache
from pathlib import Path
import os
import struct
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from jammate.instruments import gm_program
from jammate.render.midi_out import write_midi
from jammate.render.synth import mix, render


def _chunks(handle, start, end):
    position = start
    while position + 8 <= end:
        handle.seek(position)
        kind, size = struct.unpack("<4sI", handle.read(8))
        body = position + 8
        if body + size > end:
            raise ValueError("invalid SoundFont chunk length")
        yield kind, body, size
        position = body + size + size % 2


def bank_zero_programs(path):
    with Path(path).open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"sfbk":
            raise ValueError("invalid SoundFont file")
        end = 8 + struct.unpack("<I", header[4:8])[0]
        if end > os.fstat(handle.fileno()).st_size:
            raise ValueError("truncated SoundFont file")
        for kind, body, size in _chunks(handle, 12, end):
            if kind != b"LIST" or size < 4 or handle.read(4) != b"pdta":
                continue
            for kind, _, length in _chunks(handle, body + 4, body + size):
                if kind != b"phdr":
                    continue
                if length < 38 or length % 38:
                    raise ValueError("invalid SoundFont preset headers")
                presets = {}
                for _ in range(length // 38 - 1):  # The final record is the terminator.
                    name, program, bank = struct.unpack("<20sHH14x", handle.read(38))
                    title = name.split(b"\0", 1)[0].decode("latin-1").strip()
                    if bank == 0 and 0 <= program <= 127 and title:
                        presets[program] = title
                return dict(sorted(presets.items()))
    raise ValueError("SoundFont is missing preset headers")


class SoundFontRenderer:
    def __init__(self, executable, soundfont):
        self.executable = Path(executable).resolve()
        self.soundfont = Path(soundfont).resolve()
        self.programs = bank_zero_programs(self.soundfont)
        self._windows_executable = os.name != "nt" and self.executable.suffix.lower() == ".exe"
        self._bank_path = self._argument(self.soundfont)

    def _argument(self, path):
        if not self._windows_executable:
            return str(path)
        return subprocess.check_output(["wslpath", "-w", str(path)], text=True, timeout=2).strip()

    @lru_cache(maxsize=32)
    def _stem(self, notes, bpm, sample_rate, frames, programs=(), *, performance_mix=False):
        with TemporaryDirectory(prefix="jammate-soundfont-") as directory:
            midi = write_midi(notes, bpm, Path(directory) / "backing.mid", programs=dict(programs),
                              performance_mix=performance_mix)
            wave = Path(directory) / "backing.wav"
            command = [str(self.executable), "-ni", "-F", self._argument(wave), "-r", str(sample_rate),
                       "-g", "0.36", self._bank_path, self._argument(midi)]
            result = subprocess.run(command, capture_output=True, timeout=3)
            if result.returncode or not wave.is_file():
                raise ValueError("FluidSynth could not render the selected instrument")
            audio, rate = sf.read(wave, dtype="float32", always_2d=True, frames=frames)
        if rate != sample_rate or not audio.size or not np.isfinite(audio).all() or not np.any(audio):
            raise ValueError("FluidSynth did not produce valid instrument audio")
        return audio.mean(axis=1)

    def render(self, notes, bpm, sample_rate, *, total_s):
        bank_notes = tuple(note for note in notes if gm_program(note.instrument) is not None)
        if not bank_notes:
            return render(notes, bpm, sample_rate, total_s=total_s)
        if any(gm_program(note.instrument) not in self.programs for note in bank_notes):
            raise ValueError("unavailable SoundFont instrument")
        original = tuple(note for note in notes if gm_program(note.instrument) is None)
        audio = render(original, bpm, sample_rate, total_s=total_s)
        return mix(audio, self._stem(bank_notes, bpm, sample_rate, audio.size))

    def profile_timbres(self, profile):
        from renderer_experiments.app.presets import get_instrument

        if not self.programs:
            raise ValueError("SoundFont 没有可用的 Bank 0 音色。")
        preset = get_instrument(profile).programs
        first = next(iter(self.programs))
        return {name: preset[channel] if preset[channel] in self.programs else first
                for name, channel in (("keys", 1), ("bass", 2))}

    def validate_timbres(self, programs):
        if (not isinstance(programs, dict) or set(programs) != {"keys", "bass"}
                or any(type(program) is not int or program not in self.programs for program in programs.values())):
            raise ValueError("请选择当前 SoundFont 里可用的键盘和贝斯音色。")
        return dict(programs)

    def render_all(self, notes, bpm, sample_rate, *, total_s, profile="electric", timbres=None):
        frames = round(total_s * sample_rate)
        if not notes:
            return np.zeros(frames, dtype=np.float32)
        programs = self.profile_timbres(profile) if timbres is None else self.validate_timbres(timbres)
        audio = self._stem(notes, bpm, sample_rate, frames, tuple(sorted(programs.items())), performance_mix=True)
        return np.pad(audio, (0, frames - len(audio))) if len(audio) < frames else audio
