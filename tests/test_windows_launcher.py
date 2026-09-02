"""The Windows launcher has to survive Windows PowerShell 5.1.

It failed to parse on a real machine. One em dash in a log line, in a file with
no byte-order mark: PowerShell 5.1 reads a BOM-less script as Windows-1252, so
the UTF-8 em dash arrived as three characters ending in a curly quote, and
PowerShell accepts curly quotes as string delimiters. The string ended early and
the whole file failed to parse, so the extension could not start at all.

These are cheap checks for a file nothing here can execute.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "packaging" / "launcher" / "qa-copilot-launch.ps1"
BOM = b"\xef\xbb\xbf"


def test_it_is_saved_with_a_utf8_byte_order_mark():
    assert PS1.read_bytes().startswith(BOM), (
        "without a BOM, PowerShell 5.1 reads this as Windows-1252"
    )


def test_it_contains_no_characters_outside_ascii():
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    offenders = sorted({c for c in text if ord(c) > 127})
    assert not offenders, (
        f"non-ASCII in a PowerShell script is how the last one broke: {offenders}"
    )


def test_no_smart_quotes_anywhere():
    """PowerShell treats these as string delimiters, so they end strings early."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    for ch in ("“", "”", "‘", "’"):
        assert ch not in text


def test_every_string_is_closed():
    """The failure the parser reported. A scanner that strips strings cannot
    find this — an unterminated string swallows the rest of the file — so this
    walks the file and asserts it ends outside any string."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        state = None  # None, '"' or "'"
        i = 0
        while i < len(line):
            ch = line[i]
            if state is None:
                if ch == "#":
                    break                      # comment to end of line
                if ch in "\"'":
                    state = ch
            elif ch == state:
                if state == "'" and line[i + 1 : i + 2] == "'":
                    i += 1                     # '' escapes a literal quote
                else:
                    state = None
            elif state == '"' and ch == "`":
                i += 1                         # backtick escape
            i += 1
        assert state is None, f"line {number} leaves a {state} string open: {line.strip()[:70]}"


def test_powershell_7_only_syntax_is_absent():
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for token in ("??", "&&", "||"):
        assert token not in code, f"{token} needs PowerShell 7; 5.1 is the target"


# --- an actual PowerShell parse, when one is available ----------------------
# Every Windows failure so far reached the user because nothing here could run
# PowerShell. If a pwsh is present, use it: a real parse is worth more than
# every heuristic in this file put together.


def _pwsh() -> str | None:
    import shutil

    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in Path("/private/tmp").glob("**/pwsh/pwsh"):
        if candidate.is_file():
            return str(candidate)
    return None


def test_it_parses_under_real_powershell():
    import subprocess

    pwsh = _pwsh()
    if not pwsh:
        import pytest

        pytest.skip("no PowerShell available to parse with")

    script = (
        "$e = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{PS1}', "
        "[ref]$null, [ref]$e) | Out-Null; "
        "if ($e) { $e | ForEach-Object { "
        "Write-Output ('line ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }; exit 1 }"
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"PowerShell will not parse it:\n{result.stdout}"


def test_native_calls_are_guarded():
    """`& $uv venv ...` was unguarded, so a uv.exe that existed but would not
    run killed the script with 'cannot run a document in the middle of a
    pipeline' — an error a try/catch does catch."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "function Test-Runnable" in text, "an executable must be proved to run"
    assert "Test-Path $candidate -PathType Leaf" in text, "a directory is not a binary"
    for call in ("& $uv venv", "& $systemPython -m venv", "& $py -m pip install"):
        index = text.index(call)
        preceding = text[max(0, index - 400) : index]
        assert "try {" in preceding, f"{call} is not inside a try block"


def test_an_installed_python_is_preferred_over_downloading_a_runtime():
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert text.index("Find-Python") < text.index("fetching the uv runtime installer")
