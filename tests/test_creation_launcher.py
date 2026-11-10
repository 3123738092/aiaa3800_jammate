import io
import sys

import pytest

from scripts import start_input_lab


def test_launcher_refuses_legacy_workbench_on_selected_port(tmp_path, monkeypatch):
    for version in ("baseline", "optimized"):
        index = tmp_path / "web" / version / "dist" / "index.html"
        index.parent.mkdir(parents=True)
        index.touch()
    monkeypatch.setattr(start_input_lab, "__file__", str(tmp_path / "scripts" / "start_input_lab.py"))
    monkeypatch.setattr(sys, "argv", ["start_input_lab.py", "--port", "4187", "--no-browser"])
    monkeypatch.setattr(start_input_lab, "urlopen", lambda *args, **kwargs: io.BytesIO(
        b"<title>JamMate Band Lab</title>"
    ))

    with pytest.raises(RuntimeError, match="4187"):
        start_input_lab.main()
