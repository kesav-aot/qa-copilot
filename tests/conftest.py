from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def demo_server():
    """Run the demo target app for the duration of the test session."""
    port = _free_port()
    env = {**os.environ, "FLASK_APP": "demo_app.app", "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--port", str(port), "--host", "127.0.0.1"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:  # pragma: no cover
        proc.terminate()
        pytest.fail("demo app did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def _reset_demo_data(request):
    """Each test starts from the same demo data, so ordering cannot matter."""
    if "demo_server" not in request.fixturenames:
        yield
        return
    base = request.getfixturevalue("demo_server")
    import urllib.request

    def reset():
        try:
            urllib.request.urlopen(f"{base}/api/test-reset", data=b"", timeout=5).read()
        except Exception:
            pass

    reset()
    yield
    reset()


@pytest.fixture
def copilot(tmp_path, demo_server, monkeypatch):
    """A QACopilot wired to the live demo app with the demo credentials."""
    from qa_copilot.config import load_config
    from qa_copilot.engine import QACopilot

    monkeypatch.setenv("QA_SECRET__DEMO__ADMIN__USERNAME", "admin@qa.local")
    monkeypatch.setenv("QA_SECRET__DEMO__ADMIN__PASSWORD", "Adm1n-Demo-Pass!")
    monkeypatch.setenv("QA_SECRET__DEMO__USER__USERNAME", "user@qa.local")
    monkeypatch.setenv("QA_SECRET__DEMO__USER__PASSWORD", "Us3r-Demo-Pass!")

    config = load_config(ROOT / "config")
    config.environments["demo"].base_url = demo_server
    config.environments["demo"].api_base_url = demo_server
    config.artifact_dir = str(tmp_path / "artifacts")
    # Plans are written during tests; keep the repo's library untouched.
    config.plan_dir = str(tmp_path / "plans")
    config.testcase_dir = str(ROOT / "testcases")

    return QACopilot(config, state_dir=tmp_path / "state", config_dir=ROOT / "config")


@pytest.fixture(autouse=True)
def _clean_registry():
    from qa_copilot.sanitize import sanitizer

    yield
    sanitizer.registry().clear()
