"""Install the Mac's update job without restarting an active backend."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
BASE = Path.home() / "jammate-team"
AGENTS = Path.home() / "Library/LaunchAgents"
DOMAIN = f"gui/{os.getuid()}"


def install(label, argv, **settings):
    plist = AGENTS / (label + ".plist")
    with plist.open("wb") as output:
        plistlib.dump({"Label": label, "ProgramArguments": argv,
                       "WorkingDirectory": str(BASE),
                       "StandardOutPath": str(BASE / "logs" / (label + ".out.log")),
                       "StandardErrorPath": str(BASE / "logs" / (label + ".err.log")),
                       **settings}, output)
    subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{label}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(plist)], check=True)


def main():
    os.umask(0o077)
    if not (BASE / "app/web/jam.html").is_file() or not (BASE / "venv/bin/python").exists():
        raise SystemExit("未找到已有的 JamMate Mac 服务。")
    if not (BASE / "current").exists():
        (BASE / "current").symlink_to(BASE / "app", target_is_directory=True)
    for source in ("run-api.sh", "update.py"):
        shutil.copyfile(HERE / source, BASE / "bin" / source)
    AGENTS.mkdir(exist_ok=True)
    # Smart Git requests don't consume GitHub's anonymous REST API allowance.
    interval = 20 if (BASE / "bin/release-deps/dulwich").is_dir() else 90
    install("com.jammate.team.update",
            [str(BASE / "python/bin/python3.12"), str(BASE / "bin/update.py")],
            RunAtLoad=True, StartInterval=interval, ProcessType="Background")


if __name__ == "__main__":
    main()
