"""Call the team's pinned implementation without importing the local variant."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from zipfile import ZipFile


REFERENCE_COMMIT = "a63a0f5f579cbc36ed0dfa31874c8888e4873cbd"


class ReferenceBridge:
    def __init__(self, root, *, scratch, fluidsynth=None, soundfont=None):
        self.root = Path(root).resolve()
        self.scratch = Path(scratch).resolve()
        self.fluidsynth = str(Path(fluidsynth).resolve()) if fluidsynth else ""
        self.soundfont = str(Path(soundfont).resolve()) if soundfont else ""
        self._lock = Lock()
        self._groups = {"audio": Lock(), "text": Lock()}
        self._directory = None
        self._processes = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _snapshot(self):
        if self._directory is None:
            archive = subprocess.run(
                ["git", "archive", "--format=zip", REFERENCE_COMMIT, "src/jammate", "renderer_experiments/app"],
                cwd=self.root, capture_output=True,
            )
            if archive.returncode:
                raise ValueError("缺少固定的队友版本，请使用带 Git 历史的项目检出。")
            self.scratch.mkdir(parents=True, exist_ok=True)
            directory = TemporaryDirectory(prefix="reference-", dir=self.scratch)
            try:
                with ZipFile(io.BytesIO(archive.stdout)) as bundle:
                    bundle.extractall(directory.name)
            except Exception:
                directory.cleanup()
                raise
            self._directory = directory
        return Path(self._directory.name)

    def _start(self, group):
        snapshot = self._snapshot()
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (snapshot / "src", snapshot))),
                           PYTHONIOENCODING="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-u", "-B", str(self.root / "scripts/reference_worker.py"),
             str(snapshot), self.fluidsynth, self.soundfont],
            cwd=self.root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._processes[group] = process
        return process

    def request(self, operation, body):
        # A slow model request must not block the recording/analysis entry.
        group = "text" if operation.startswith("text/") else "audio"
        with self._groups[group]:
            with self._lock:
                process = self._processes.get(group) or self._start(group)
            try:
                process.stdin.write(json.dumps({"operation": operation, "body": body}, allow_nan=False) + "\n")
                process.stdin.flush()
                reply = json.loads(process.stdout.readline())
            except (OSError, json.JSONDecodeError):
                with self._lock:
                    self._processes.pop(group)
                    self._stop(process)
                raise ValueError("队友版本的服务已中断，请重试。文字作品需要重新开始。") from None
        if "error" in reply:
            raise ValueError(reply["error"])
        if operation == "rhythm/analyze":
            return reply["result"]
        return {**reply["result"], "reference_commit": REFERENCE_COMMIT}

    @staticmethod
    def _stop(process):
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        finally:
            process.stdout.close()

    def close(self):
        with self._groups["audio"], self._groups["text"], self._lock:
            for process in self._processes.values():
                self._stop(process)
            self._processes.clear()
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None
