"""Writing into another application's settings file must be conservative:
never lose what is already there, and always leave a way back."""

from __future__ import annotations

import json

import pytest

from qa_copilot.cli import _desktop_config_path, _install_desktop

SERVER = {
    "command": "/opt/qa-copilot/.venv/bin/qa-copilot-mcp",
    "args": [],
    "env": {"QA_COPILOT_CONFIG": "/opt/qa-copilot/config"},
}

EXISTING = {
    "coworkUserFilesPath": "/Users/someone/Claude",
    "preferences": {"sidebarMode": "chat", "nested": {"deep": [1, 2]}},
}


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr("qa_copilot.cli._desktop_config_path", lambda: path)
    return path


def test_the_config_path_is_platform_specific(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert _desktop_config_path().name == "claude_desktop_config.json"
    assert "Application Support/Claude" in str(_desktop_config_path())


def test_existing_settings_are_kept(desktop):
    desktop.write_text(json.dumps(EXISTING))
    assert _install_desktop(SERVER, force=False) == 0

    written = json.loads(desktop.read_text())
    assert written["coworkUserFilesPath"] == EXISTING["coworkUserFilesPath"]
    assert written["preferences"] == EXISTING["preferences"]
    assert written["mcpServers"]["qa-copilot"] == SERVER


def test_other_mcp_servers_are_kept(desktop):
    desktop.write_text(json.dumps({"mcpServers": {"filesystem": {"command": "x"}}}))
    _install_desktop(SERVER, force=False)
    servers = json.loads(desktop.read_text())["mcpServers"]
    assert set(servers) == {"filesystem", "qa-copilot"}


def test_a_backup_is_written(desktop):
    desktop.write_text(json.dumps(EXISTING))
    _install_desktop(SERVER, force=False)
    backups = list(desktop.parent.glob("*.backup-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == EXISTING


def test_installing_when_there_is_no_config_yet(desktop):
    assert _install_desktop(SERVER, force=False) == 0
    assert json.loads(desktop.read_text())["mcpServers"]["qa-copilot"] == SERVER
    assert not list(desktop.parent.glob("*.backup-*")), "nothing to back up"


def test_an_existing_entry_is_not_replaced_silently(desktop):
    desktop.write_text(json.dumps({"mcpServers": {"qa-copilot": {"command": "old"}}}))
    assert _install_desktop(SERVER, force=False) == 1
    assert json.loads(desktop.read_text())["mcpServers"]["qa-copilot"]["command"] == "old"


def test_force_replaces_it(desktop):
    desktop.write_text(json.dumps({"mcpServers": {"qa-copilot": {"command": "old"}}}))
    assert _install_desktop(SERVER, force=True) == 0
    assert json.loads(desktop.read_text())["mcpServers"]["qa-copilot"] == SERVER


def test_a_corrupt_settings_file_is_reported_not_overwritten(desktop):
    desktop.write_text("{ this is not json")
    assert _install_desktop(SERVER, force=False) == 2
    assert desktop.read_text() == "{ this is not json"


def test_a_missing_claude_folder_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qa_copilot.cli._desktop_config_path",
        lambda: tmp_path / "nope" / "claude_desktop_config.json",
    )
    assert _install_desktop(SERVER, force=False) == 2


def test_the_written_file_is_valid_json_a_gui_can_read(desktop):
    desktop.write_text(json.dumps(EXISTING))
    _install_desktop(SERVER, force=False)
    text = desktop.read_text()
    assert text.endswith("\n")
    json.loads(text)


# --- the trap that made Claude Desktop fail ---------------------------------

@pytest.mark.parametrize("folder", ["Documents", "Desktop", "Downloads", "Pictures"])
def test_a_macos_protected_folder_is_warned_about(folder, monkeypatch):
    """macOS refuses to let a sandboxed app execute anything in these folders.
    Claude's own log only says 'Operation not permitted', so `doctor` says it
    plainly instead."""
    from pathlib import Path

    from qa_copilot.cli import _tcc_warning

    monkeypatch.setattr("sys.platform", "darwin")
    warning = _tcc_warning(Path.home() / folder / "some-project")
    assert warning is not None
    assert folder in warning
    assert "Claude Desktop" in warning


def test_an_unprotected_folder_is_not_warned_about(monkeypatch):
    from pathlib import Path

    from qa_copilot.cli import _tcc_warning

    monkeypatch.setattr("sys.platform", "darwin")
    assert _tcc_warning(Path.home() / "qa-copilot") is None
    assert _tcc_warning(Path("/opt/qa-copilot")) is None


def test_the_warning_is_macos_only(monkeypatch):
    from pathlib import Path

    from qa_copilot.cli import _tcc_warning

    monkeypatch.setattr("sys.platform", "linux")
    assert _tcc_warning(Path.home() / "Documents" / "x") is None
