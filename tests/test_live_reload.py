"""Setup finishing must be enough. The reported failure: the setup page said it
had finished, and every list stayed empty until Claude Desktop was restarted,
because the server read its configuration once and never again."""

from __future__ import annotations

import json
import os

import pytest

from qa_copilot import mcp_server
from qa_copilot.secrets import env as env_secrets

ENVIRONMENT = """environments:
  qa:
    base_url: https://forms.example.test
"""
IDENTITY = """identities:
  DESIGNER_USER:
    capabilities: [design_forms]
    username_ref: secret://qa/designer/username
    password_ref: secret://qa/designer/password
    environments: [qa]
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("secrets:\n  provider: env\n  dotenv: .env\n")
    monkeypatch.setenv("QA_COPILOT_CONFIG", str(config))
    monkeypatch.setenv("QA_COPILOT_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(mcp_server, "_copilot", None)
    monkeypatch.setattr(mcp_server, "_loaded_stamp", ())
    return config


def _call(tool) -> dict:
    return json.loads(tool())


def test_what_setup_writes_is_seen_without_a_restart(server):
    assert _call(mcp_server.list_environments)["environments"] == []
    assert _call(mcp_server.list_identities)["identities"] == []

    (server / "environments.yaml").write_text(ENVIRONMENT)
    (server / "identities.yaml").write_text(IDENTITY)

    assert [e["name"] for e in _call(mcp_server.list_environments)["environments"]] == ["qa"]
    assert _call(mcp_server.list_capabilities)["capabilities"] == ["design_forms"]
    alias = _call(mcp_server.list_identities)["identities"][0]["alias"]
    assert alias == "DESIGNER_USER"


def test_nothing_is_reloaded_when_nothing_changed(server):
    first = mcp_server.copilot()
    assert mcp_server.copilot() is first


def test_a_half_written_file_does_not_take_the_tools_down(server):
    (server / "environments.yaml").write_text(ENVIRONMENT)
    assert _call(mcp_server.list_environments)["environments"]

    (server / "environments.yaml").write_text("environments:\n  qa: [unclosed\n")
    # Still answering, from what was last read correctly...
    assert [e["name"] for e in _call(mcp_server.list_environments)["environments"]] == ["qa"]

    # ...and the file is read again once it is whole.
    (server / "environments.yaml").write_text(
        ENVIRONMENT + "  staging:\n    base_url: https://staging.example.test\n"
    )
    names = [e["name"] for e in _call(mcp_server.list_environments)["environments"]]
    assert names == ["qa", "staging"]


def test_a_password_entered_again_replaces_the_old_one(tmp_path, monkeypatch):
    """setdefault kept whichever value was loaded first, so re-running setup
    with a corrected password changed the file and not the password used."""
    var = "QA_SECRET__QA__RELOAD__PASSWORD"
    monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(env_secrets, "_FROM_DOTENV", set())
    dotenv = tmp_path / ".env"

    dotenv.write_text(f"{var}=first-value\n")
    env_secrets.load_dotenv(dotenv)
    assert os.environ[var] == "first-value"

    dotenv.write_text(f"{var}=second-value\n")
    env_secrets.load_dotenv(dotenv)
    assert os.environ[var] == "second-value"


def test_a_real_environment_variable_still_wins_over_the_file(tmp_path, monkeypatch):
    var = "QA_SECRET__QA__SHELL__PASSWORD"
    monkeypatch.setenv(var, "from-the-shell")
    monkeypatch.setattr(env_secrets, "_FROM_DOTENV", set())
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{var}=from-the-file\n")
    env_secrets.load_dotenv(dotenv)
    assert os.environ[var] == "from-the-shell"
