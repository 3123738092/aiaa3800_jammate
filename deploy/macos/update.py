"""Download and roll out the trusted JamMate main branch on the team Mac."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen


BASE = Path.home() / "jammate-team"
REPO = "3123738092/aiaa3800_jammate"
API = f"https://api.github.com/repos/{REPO}/git/ref/heads/main"
SERVICE = f"gui/{os.getuid()}/com.jammate.team.api"
API_PORT = 4215
CANDIDATE_PORT = 4216
MANIFESTS = ("pyproject.toml", "requirements-input.lock.txt", "requirements-mrt2-linux.lock.txt")
HEADERS = {"User-Agent": "JamMate-Team-Release/1"}


def report(message):
    print(message, flush=True)


def fetch(url, timeout=30):
    return urlopen(Request(url, headers=HEADERS), timeout=timeout)


def backend(port, path="/api/ready", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def committed_release(directory):
    file = directory / ".release"
    return file.read_text().strip() if file.is_file() else None


def wait_for(port, sha, process=None):
    deadline = monotonic() + 35
    while monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("候选后端退出，请查看 candidate.log。")
        try:
            ready = backend(port)
            if ready.get("release") == sha and ready.get("implementation") == "workspace":
                return
        except (URLError, TimeoutError, ValueError):
            pass
        sleep(.5)
    raise RuntimeError(f"后端未能报告预期版本 {sha}。")


def download_release(base, sha):
    root = base / "releases"
    root.mkdir(exist_ok=True)
    release = root / sha
    if committed_release(release) == sha:
        return release
    with TemporaryDirectory(prefix="checkout-", dir=root) as temp:
        working = Path(temp)
        archive = working / "source.tar.gz"
        url = f"https://codeload.github.com/{REPO}/tar.gz/{sha}"
        with fetch(url, timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(working, filter="data")
        checkout = working / f"{REPO.split('/')[1]}-{sha}"
        if not (checkout / "web/jam.html").is_file() or not (checkout / "src/jammate/input_server.py").is_file():
            raise RuntimeError("GitHub 下载的提交缺少 Studio 文件。")
        (checkout / ".release").write_text(sha + "\n")
        checkout.rename(release)
    return release


def prepare_runtime(base, current, release):
    python_file = release / ".python"
    if python_file.exists():
        return
    old = current / ".python"
    if all((current / name).exists() and (release / name).exists()
           and (current / name).read_text() == (release / name).read_text() for name in MANIFESTS):
        runtime = old.read_text().strip() if old.exists() else str(base / "venv/bin/python")
    else:
        uv = base / "bin/uv"
        venv = release / ".venv"
        python = venv / "bin/python"
        subprocess.run([uv, "venv", "--allow-existing", "--python", base / "python/bin/python3.12", venv], check=True)
        subprocess.run([uv, "pip", "install", "--python", python, "--find-links", base / "wheels", "-e", ".[llm]",
                        "magenta-rt[mlx]==2.0.3", "mlx==0.31.2", "mlx-metal==0.31.2"], cwd=release, check=True)
        runtime = str(python)
    python_file.write_text(runtime + "\n")


def check_candidate(base, release, sha):
    with (base / "logs/candidate.log").open("ab") as output:
        process = subprocess.Popen(["/bin/bash", base / "bin/run-api.sh", release, str(CANDIDATE_PORT)],
                                   stdout=output, stderr=subprocess.STDOUT)
        try:
            wait_for(CANDIDATE_PORT, sha, process)
            features = backend(CANDIDATE_PORT, "/api/jam/status", {})
            missing = [key for key in ("studio_v4", "llm_ready", "speech_ready", "mrt2") if not features.get(key)]
            if missing:
                raise RuntimeError(f"候选版本缺少团队能力：{', '.join(missing)}")
            jam = backend(CANDIDATE_PORT, "/api/jam/start", {"renderer": "synth", "agent": "rule",
                          "opening_mode": "cold", "opening": {"text": "完整乐队伴奏，96 BPM"}})
            try:
                if not jam["next"]["wav"]:
                    raise RuntimeError("候选版本未能渲染开场。")
            finally:
                backend(CANDIDATE_PORT, "/api/jam/stop", {"session": jam["session"]})
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def point(link, path):
    pending = link.with_suffix(".new")
    pending.unlink(missing_ok=True)
    pending.symlink_to(path, target_is_directory=True)
    pending.replace(link)


def legacy_session(base):
    log = base / "logs/api.err.log"
    if not log.exists():
        return False
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 65536))
        tail = stream.read().decode(errors="replace")
    recent = re.findall(r'\[([^]]+)\] "POST /api/jam/(?:start|poll|studio-input|feedback) HTTP/', tail)
    if not recent:
        return False
    return (datetime.now() - datetime.strptime(recent[-1], "%d/%b/%Y %H:%M:%S")).total_seconds() <= 90


def active_session(base):
    try:
        status = backend(API_PORT)
    except URLError as error:
        # An offline API has no live sessions. Let the validated release restore it.
        if isinstance(error.reason, ConnectionRefusedError):
            return False
        raise
    if "active_sessions" not in status:
        return legacy_session(base)
    return status.get("active_sessions", 0) or status.get("pending_starts", 0)


def restart_api(base):
    plist = Path.home() / "Library/LaunchAgents/com.jammate.team.api.plist"
    argv = ["/bin/bash", str(base / "bin/run-api.sh")]
    with plist.open("rb") as stream:
        settings = plistlib.load(stream)
    domain = SERVICE.rsplit("/", 1)[0]
    if settings["ProgramArguments"] == argv:
        result = subprocess.run(["launchctl", "kickstart", "-k", SERVICE], capture_output=True)
        if result.returncode:
            subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
        return
    settings["ProgramArguments"] = argv
    pending = plist.with_suffix(".new")
    with pending.open("wb") as stream:
        plistlib.dump(settings, stream)
    pending.replace(plist)
    subprocess.run(["launchctl", "bootout", SERVICE], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)


def activate(base, release):
    current = base / "current"
    previous = current.resolve(strict=True)
    if active_session(base):
        report("Jam 正在演奏，等会话结束后再发布。")
        return False
    drain = base / "config/deploy-drain"
    drain.touch()
    try:
        if active_session(base):
            return False
        point(current, release)
        try:
            restart_api(base)
            wait_for(API_PORT, committed_release(release))
        except (RuntimeError, OSError, subprocess.CalledProcessError):
            point(current, previous)
            restart_api(base)
            wait_for(API_PORT, committed_release(previous))
            raise
        point(base / "previous", previous)
        report(f"已发布 {committed_release(release) or '初始版本'}")
        return True
    finally:
        drain.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", action="store_true", help="切回上一版本并暂停自动发布")
    args = parser.parse_args()
    os.umask(0o077)
    base = BASE
    pause = base / "config/deploy-paused"
    available = base / "config/deploy-available"
    with (base / "config/deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # Recover if the previous updater exited during cutover.
        (base / "config/deploy-drain").unlink(missing_ok=True)
        if args.rollback:
            available.unlink(missing_ok=True)
            if activate(base, (base / "previous").resolve(strict=True)):
                pause.touch()
            return
        if pause.exists():
            available.unlink(missing_ok=True)
            return
        with fetch(API) as response:
            sha = json.load(response)["object"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise RuntimeError("main 的提交号无效。")
        current = (base / "current").resolve(strict=True)
        if committed_release(current) == sha:
            available.unlink(missing_ok=True)
            return
        if available.exists() and available.read_text().strip() != sha:
            available.unlink()
        release = download_release(base, sha)
        prepare_runtime(base, current, release)
        checked = release / ".checked"
        if not checked.exists():
            check_candidate(base, release, sha)
            checked.touch()
        pending = available.with_suffix(".new")
        pending.write_text(sha + "\n")
        pending.replace(available)
        try:
            if activate(base, release):
                available.unlink(missing_ok=True)
        except (RuntimeError, OSError, subprocess.CalledProcessError):
            available.unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    main()
