"""The credential guard runs in the harness, not in a prompt — so test it there."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "credential_guard.py"


def run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


BLOCKED = [
    "the password is Adm1n-Demo-Pass!",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.abcd1234",
    "use sk-live_abcdefghijklmnopqrstuvwx",
    "connect to postgres://qa:hunter2pw@db.internal/app",
    "-----BEGIN RSA PRIVATE KEY-----",
    "AKIAIOSFODNN7EXAMPLE",
]

ALLOWED = [
    "log in as ADMIN_USER and open /users",
    "use the identity with capability manage_settings",
    "the password field should show an error when left blank",
    "reference secret://demo/admin/password in the plan",
]


@pytest.mark.parametrize("prompt", BLOCKED)
def test_credential_shaped_prompts_are_blocked(prompt):
    result = run({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
    assert result.returncode == 2, result.stdout
    assert "secret://" in result.stderr


@pytest.mark.parametrize("prompt", ALLOWED)
def test_alias_based_prompts_pass_through(prompt):
    assert run({"hook_event_name": "UserPromptSubmit", "prompt": prompt}).returncode == 0


def test_tool_input_is_scanned_too():
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"content": "password = SuperSecret123"},
    }
    assert run(payload).returncode == 2


def test_malformed_input_does_not_wedge_the_harness():
    result = subprocess.run(
        [sys.executable, str(GUARD)], input="not json", capture_output=True, text=True
    )
    assert result.returncode == 0
