"""Download and roll out the trusted JamMate main branch on the team Mac."""
import argparse
from datetime import datetime
import fcntl
from http.client import HTTPException
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen


BASE = Path.home() / "jammate-team"
REPO = "3123738092/aiaa3800_jammate"
GIT_HOST = "github.com"
GIT_PATH = f"/{REPO}.git"
MAIN_REF = b"refs/heads/main"
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


def main_revision(base):
    dependencies = base / "bin/release-deps"
    if not (dependencies / "dulwich").is_dir():
        with fetch(API, timeout=10) as response:
            return json.load(response)["object"]["sha"], None

    sys.path.insert(0, str(dependencies))
    from dulwich.client import Urllib3HttpGitClient
    from dulwich.errors import GitProtocolError
    from urllib3 import PoolManager, Timeout

    config = base / "config/github-addresses"
    addresses = [str(IPv4Address(value)) for value in config.read_text().split()] if config.is_file() else []
    for address in [*addresses, GIT_HOST]:
        # Keep GitHub's HTTP host, TLS SNI, and certificate checks when overriding DNS.
        pool = PoolManager(server_hostname=GIT_HOST, assert_hostname=GIT_HOST, headers={"Host": GIT_HOST},
                           retries=False, timeout=Timeout(connect=4, read=35))
        client = Urllib3HttpGitClient(f"https://{address}", pool_manager=pool)
        try:
            refs = client.get_refs(GIT_PATH, ref_prefix=[MAIN_REF], protocol_version=0)
            return refs.refs[MAIN_REF].decode("ascii"), client
        except (GitProtocolError, OSError, KeyError) as error:
            pool.clear()
            last_error = error
    raise RuntimeError("无法查询 GitHub main。") from last_error


def archive_revision(base, sha, client, output):
    from dulwich.porcelain import archive
    from dulwich.repo import Repo

    cache = base / "deploy-source.git"
    commit = sha.encode("ascii")
    with (Repo(str(cache)) if cache.exists() else Repo.init_bare(str(cache), mkdir=True)) as repo:
        if commit not in repo.object_store:
            client.fetch(GIT_PATH, repo, determine_wants=lambda refs, depth: [commit], depth=1,
                         ref_prefix=[MAIN_REF])
        with output.open("wb") as stream:
            archive(repo, commit, outstream=stream)
        repo.refs[MAIN_REF] = commit


def publish_progress(path, sha, phase):
    pending = path.with_suffix(".new")
    pending.write_text(json.dumps({"release": sha, "phase": phase}), encoding="ascii")
    pending.replace(path)


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


def download_release(base, sha, client):
    root = base / "releases"
    root.mkdir(exist_ok=True)
    release = root / sha
    if committed_release(release) == sha:
        return release
    with TemporaryDirectory(prefix="checkout-", dir=root) as temp:
        working = Path(temp)
        source = working / "source.tar"
        checkout = working / "source"
        if client is None:
            url = f"https://codeload.github.com/{REPO}/tar.gz/{sha}"
            with fetch(url, timeout=90) as response, source.open("wb") as output:
                shutil.copyfileobj(response, output)
            checkout = working / f"{REPO.split('/')[1]}-{sha}"
        else:
            archive_revision(base, sha, client, source)
        with tarfile.open(source, "r:*") as bundle:
            bundle.extractall(working if client is None else checkout, filter="data")
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
    progress = base / "config/deploy-status.json"
    with (base / "config/deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # Recover if the previous updater exited during cutover.
        (base / "config/deploy-drain").unlink(missing_ok=True)
        if args.rollback:
            available.unlink(missing_ok=True)
            progress.unlink(missing_ok=True)
            if activate(base, (base / "previous").resolve(strict=True)):
                pause.touch()
            return
        if pause.exists():
            available.unlink(missing_ok=True)
            progress.unlink(missing_ok=True)
            return
        sha, client = main_revision(base)
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise RuntimeError("main 的提交号无效。")
        current = (base / "current").resolve(strict=True)
        if committed_release(current) == sha:
            available.unlink(missing_ok=True)
            progress.unlink(missing_ok=True)
            return
        if available.exists() and available.read_text().strip() != sha:
            available.unlink()
        release = base / "releases" / sha
        if not (release / ".checked").exists():
            publish_progress(progress, sha, "preparing")
            try:
                release = download_release(base, sha, client)
                prepare_runtime(base, current, release)
                check_candidate(base, release, sha)
                (release / ".checked").touch()
            except Exception:
                publish_progress(progress, sha, "delayed")
                raise
        pending = available.with_suffix(".new")
        pending.write_text(sha + "\n")
        pending.replace(available)
        try:
            if activate(base, release):
                available.unlink(missing_ok=True)
            progress.unlink(missing_ok=True)
        except (RuntimeError, OSError, subprocess.CalledProcessError, HTTPException):
            available.unlink(missing_ok=True)
            publish_progress(progress, sha, "delayed")
            raise


if __name__ == "__main__":
    main()
