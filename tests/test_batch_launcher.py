"""The Windows launcher is batch, and this is what can be checked without cmd.

PowerShell cost five releases on Windows, every one of them an assumption about
the environment Claude Desktop provides: a stripped PATH, no PATHEXT, argument
quoting that dropped anything with a space, and an encoding rule that turned one
dash into a parse error. cmd.exe starts programs directly and has none of that.

Nothing here can run cmd.exe, so these checks are structural — but the first one
is the one that matters most, and it is exact.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMD = ROOT / "packaging" / "launcher" / "qa-copilot-launch.cmd"


def _text() -> str:
    return CMD.read_bytes().decode("ascii").replace("\r\n", "\n")


def test_it_is_ascii_with_crlf_endings():
    raw = CMD.read_bytes()
    raw.decode("ascii")  # raises if not
    assert b"\r\n" in raw, "cmd.exe wants CRLF"


def test_nothing_is_written_to_stdout():
    """Stdout is the MCP channel. One stray echo corrupts the protocol, and the
    host then reports a parse error that says nothing about the cause."""
    offenders = []
    for number, line in enumerate(_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped.lower().startswith("echo"):
            continue
        if stripped.lower().startswith("echo off"):
            continue
        # Redirected to stderr, to a file, or piped into something.
        if "1>&2" in stripped or ">" in stripped or "|" in stripped:
            continue
        offenders.append((number, stripped))
    assert not offenders, f"these write to stdout: {offenders}"


def test_every_label_that_is_called_exists():
    text = _text()
    labels = {m.group(1).lower() for m in re.finditer(r"^:(\w+)", text, re.MULTILINE)}
    referenced = {m.group(1).lower() for m in re.finditer(r"(?:goto|call)\s+:(\w+)", text)}
    missing = referenced - labels - {"eof"}
    assert not missing, f"called but never defined: {sorted(missing)}"


def test_python_is_found_without_relying_on_path():
    """The failure that took three releases to see: Desktop starts this without
    the user's PATH, so a per-user Python install is invisible there."""
    text = _text()
    assert "PythonCore" in text, "the registry records where Python installed itself"
    assert "Programs\\Python\\Python3*" in text, "and the standard install folder"
    assert text.index("PythonCore") < text.index("where python"), (
        "PATH must be the last resort, not the first"
    )


def test_windowsapps_stubs_are_skipped():
    assert "WindowsApps" in _text(), "those are stubs that open the Microsoft Store"


def test_the_interpreter_is_version_checked_before_use():
    text = _text()
    assert "sys.version_info >= (3, 11)" in text
    assert "is not usable" in text, "and a rejection must say so"


def test_provisioning_is_serialised_and_released():
    text = _text()
    assert 'md "%LOCK%"' in text, "md fails when the directory exists, so it is the lock"
    assert ":unlock" in text
    for guard in ("exit /b 1",):
        assert guard in text
    # Every early exit must release the lock first, or the next start waits.
    for match in re.finditer(r"^(\s*)exit /b 1\s*$", text, re.MULTILINE):
        preceding = text[max(0, match.start() - 300) : match.start()]
        assert "call :unlock" in preceding or "cannot find the QA Copilot package" in preceding, (
            "an exit that keeps the lock blocks every later start"
        )


def test_the_server_is_checked_before_it_is_run():
    text = _text()
    assert 'if not exist "%SERVER%"' in text
    assert "the server is missing at" in text


def test_placeholders_from_the_host_are_treated_as_unset():
    """Desktop leaves ${user_config.x} in place when a field is left blank."""
    text = _text()
    assert ":clean" in text
    assert "${=" in text, "the check is for a literal ${ in the value"


def test_a_completed_install_never_waits_for_the_lock():
    """The hang: a lock left by one of the crashed PowerShell starts blocked
    every later start. The host cancels an initialize after about a minute, so
    a launcher that waits longer than that has simply stopped working."""
    text = _text()
    assert "NEEDSETUP" in text
    assert text.index("if not defined NEEDSETUP goto :ready") < text.index(":lock"), (
        "the check must come before the lock is touched"
    )
    assert ":ready" in text


def test_the_wait_is_shorter_than_the_hosts_own_timeout():
    text = _text()
    match = re.search(r"if %TRIES% GEQ (\d+)", text)
    assert match, "the wait must be bounded"
    tries = int(match.group(1))
    # ping -n 3 is about two seconds per attempt.
    assert tries * 2 <= 30, f"{tries} attempts is longer than a host will wait"
    assert "taking it over" in text, "and it must then break the lock, not give up"
