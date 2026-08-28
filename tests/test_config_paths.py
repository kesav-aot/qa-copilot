"""Relative paths in the config must resolve against the project, not the
process's working directory.

Claude Desktop launches MCP servers from `/`. Before this, that meant no
credentials, no plans and no test cases — the server started and did nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from qa_copilot.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """Run as if launched from an unrelated directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_relative_paths_hang_off_the_config_directory(elsewhere):
    config = load_config(ROOT / "config")
    assert config.root == ROOT
    assert config.plans_path == ROOT / "plans"
    assert config.testcases_path == ROOT / "testcases"
    assert config.artifacts_path == ROOT / "artifacts"
    assert config.dotenv_path == ROOT / ".env"


def test_absolute_paths_are_left_alone(elsewhere):
    config = load_config(ROOT / "config")
    config.artifact_dir = str(elsewhere / "somewhere-else")
    assert config.artifacts_path == elsewhere / "somewhere-else"


def test_secrets_resolve_from_an_unrelated_working_directory(elsewhere, monkeypatch):
    """The failure this fixes: credentials silently unavailable."""
    from qa_copilot.engine import QACopilot

    for key in list(os.environ):
        if key.startswith("QA_SECRET__"):
            monkeypatch.delenv(key, raising=False)

    copilot = QACopilot.load(ROOT / "config", elsewhere / "state")
    identities = copilot.list_identities()
    assert identities, "config should still load"
    assert all(i["credentials_configured"] for i in identities), (
        "the .env beside the config must be found from any working directory"
    )


def test_the_plan_library_is_found_from_an_unrelated_directory(elsewhere):
    from qa_copilot.engine import QACopilot

    copilot = QACopilot.load(ROOT / "config", elsewhere / "state")
    assert copilot.list_plans()["plans"], "plans/ must resolve against the project"


def test_test_cases_are_found_from_an_unrelated_directory(elsewhere):
    from qa_copilot.engine import QACopilot

    copilot = QACopilot.load(ROOT / "config", elsewhere / "state")
    result = copilot.ingest_test_cases()
    assert result["cases"], "testcases/ must resolve against the project"
    assert not result["errors"]


def test_the_state_directory_defaults_beside_the_config(elsewhere):
    from qa_copilot.engine import QACopilot

    copilot = QACopilot(load_config(ROOT / "config"), config_dir=ROOT / "config")
    assert copilot.state == ROOT / ".qa-copilot"
