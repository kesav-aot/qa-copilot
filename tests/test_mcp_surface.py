"""The MCP tool surface is a security boundary: assert what it does *not* expose."""

from __future__ import annotations

import asyncio

from qa_copilot import mcp_server


def registered_tools():
    """The tools as the MCP client actually sees them, not module functions."""
    return asyncio.run(mcp_server.mcp.list_tools())


def tool_names() -> set[str]:
    return {t.name for t in registered_tools()}


FORBIDDEN = {
    "get_secret", "read_secret", "resolve_secret", "get_password", "get_username",
    "get_api_key", "read_env", "read_file", "write_file", "dump_config",
    "run_javascript", "evaluate", "execute_shell", "approve_plan", "approve",
    "set_policy", "add_identity", "edit_config",
}


def test_no_secret_reading_or_self_approval_tools_exist():
    assert tool_names() & FORBIDDEN == set()


def test_expected_tools_are_present():
    expected = {
        # MVP 1
        "list_environments", "list_identities", "list_capabilities",
        "get_test_plan_schema", "validate_test_plan", "run_test_plan", "recent_activity",
        # MVP 2
        "list_test_cases", "analyze_test_case", "draft_plan_from_test_case",
        "save_test_plan", "list_test_plans", "get_test_plan", "run_test_suite",
        # MVP 3 — plain English
        "get_phrasebook", "check_plain_test", "run_plain_test",
    }
    assert expected <= tool_names()


def test_every_tool_documents_itself():
    """A tool description is the model's only instruction for using it."""
    for tool in registered_tools():
        assert tool.description and len(tool.description) > 60, (
            f"{tool.name} needs a real docstring"
        )


def test_only_the_writing_tools_are_marked_non_read_only():
    writing = {
        t.name
        for t in registered_tools()
        if t.annotations and t.annotations.read_only_hint is False
    }
    assert writing == {
        "run_test_plan",
        "run_test_suite",
        "run_plain_test",
        "save_test_plan",
        # Writes configuration and the secret store — via a page the human fills
        # in, not from anything the model passes. It takes no arguments at all.
        "open_setup",
    }


def test_the_only_filesystem_reach_is_the_testcase_directory(tmp_path, monkeypatch):
    """list_test_cases takes a path from the model. It must not become a file
    reader for the rest of the disk."""
    from qa_copilot.engine import QACopilot
    from qa_copilot.config import load_config
    from pathlib import Path as _Path

    config = load_config(_Path("config"))
    config.testcase_dir = str(tmp_path)
    copilot = QACopilot(config, state_dir=tmp_path / "state")
    monkeypatch.setattr(mcp_server, "copilot", lambda: copilot)

    for attempt in ["../../../etc/passwd", "/etc/hosts", "../config/identities.yaml"]:
        out = mcp_server.list_test_cases(attempt)
        assert "outside the test-case directory" in out


def test_egress_guard_discards_a_leaked_secret(monkeypatch, tmp_path):
    from qa_copilot.sanitize import sanitizer

    sanitizer.registry().register("leaky-secret-value")
    monkeypatch.setattr(sanitizer, "scrub", lambda payload: payload)  # simulate a sanitiser bug

    class FakeAudit:
        def write(self, *a, **kw):
            pass

    class FakeCopilot:
        audit = FakeAudit()

    monkeypatch.setattr(mcp_server, "copilot", lambda: FakeCopilot())
    out = mcp_server._safe({"oops": "leaky-secret-value"})
    assert "SECURITY_VIOLATION" in out
    assert "leaky-secret-value" not in out
    sanitizer.registry().clear()


def test_the_setup_tool_cannot_be_handed_a_credential():
    """open_setup exists so nobody types a password to the model.

    If it ever grew a parameter, a model could be talked into passing one — so
    the absence of arguments is the guarantee, and it is worth asserting.
    """
    tool = next(t for t in registered_tools() if t.name == "open_setup")
    properties = (tool.input_schema or {}).get("properties") or {}
    assert properties == {}, f"open_setup must take no arguments, got {sorted(properties)}"


def test_the_documented_tool_count_matches_reality():
    """The README, CONNECT.md and MANUAL-TESTING.md all quoted a count, and all
    three had drifted — to fourteen, seventeen and fourteen against nineteen
    tools. A number in prose is only useful if it is checked."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    actual = len(registered_tools())

    # Counts that were once right and silently stopped being so. A smaller
    # number like "7 tools" also appears deliberately, in troubleshooting rows
    # describing a stale connection, so this checks named staleness rather than
    # every digit followed by the word "tools".
    stale = {n: w for n, w in
             {14: "fourteen", 17: "seventeen", 19: "nineteen", 20: "twenty"}.items()
             if n != actual}

    for name in ("README.md", "docs/CONNECT.md", "docs/MANUAL-TESTING.md"):
        text = (root / name).read_text()
        current = {14: "fourteen", 17: "seventeen", 19: "nineteen", 20: "twenty"}[actual]
        assert f"{actual} tools" in text or f"{current} MCP tools" in text, (
            f"{name} never states the tool count"
        )
        for number, word in stale.items():
            assert f"{number} tools" not in text, f"{name} still says {number} tools"
            assert f"{word} MCP tools" not in text, f"{name} still says {word}"
