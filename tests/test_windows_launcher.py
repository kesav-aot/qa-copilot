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
    """A fallback for machines with no PowerShell.

    This is a heuristic, and it false-positives on regex literals containing
    quotes, so it steps aside when a real parser is available — that test is
    strictly better. It stays for CI images without pwsh, where it is the only
    thing standing between an unterminated string and a shipped release.
    """
    if _pwsh():
        import pytest

        pytest.skip("test_it_parses_under_real_powershell covers this properly")
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


def test_every_program_call_reports_its_exit_code():
    """`& $uv venv ...` was unguarded, so a uv that would not run killed the
    script outright. Now every call returns a code that is checked, rather than
    throwing from the middle of a pipeline."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "function Test-Runnable" in text, "an executable must be proved to run"
    assert "Test-Path $candidate -PathType Leaf" in text, "a directory is not a binary"
    for call in ("Invoke-Exe $uv", "Invoke-Exe $exe", "Invoke-Exe $py"):
        assert call in text, f"{call} missing"
    assert text.count("$result.Code -ne 0") >= 2, "exit codes must be acted on"


def test_an_installed_python_is_preferred_over_downloading_a_runtime():
    """Downloading a runtime to reach an interpreter already on the machine is
    slower and one more thing to fail — it did fail, twice."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert text.index("New-Environment $venv") < text.index("fetching the uv runtime installer")


def test_a_uv_that_will_not_run_is_discarded_before_reinstalling():
    """Leaving it there means the installer writes beside a file that failed."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "discarding a uv that will not run" in text
    assert "Remove-Item -Force $stale" in text


def _run_pwsh(script: str):
    """Load the launcher's functions and run `script` against them."""
    import subprocess

    pwsh = _pwsh()
    if not pwsh:
        import pytest

        pytest.skip("no PowerShell available")
    preamble = (
        "$e = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{PS1}', "
        "[ref]$null, [ref]$e); "
        "$ast.FindAll({ $args[0] -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | "
        "ForEach-Object { Invoke-Expression $_.Extent.Text }; "
    )
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", preamble + script],
        capture_output=True, text=True, timeout=300,
    )


def test_candidates_are_objects_not_nested_arrays():
    """The bug this pins: candidates were `,@($exe, $selector)` pairs, and
    PowerShell unwraps a single-element array of arrays. The loop then iterated
    the characters of the path and tried to run a command called "/". Any
    machine with exactly one usable Python hit it."""
    result = _run_pwsh(
        "$c = @(Get-PythonCandidates); "
        "if ($c.Count -eq 0) { Write-Output 'none'; exit 0 }; "
        "foreach ($x in $c) { "
        "  if (-not $x.Exe) { Write-Output 'NOT-AN-OBJECT'; exit 1 } "
        "}; Write-Output 'objects'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NOT-AN-OBJECT" not in result.stdout


def test_a_single_candidate_is_still_iterated_as_a_candidate():
    """Forces the one-element case, which is where the unwrapping showed up."""
    result = _run_pwsh(
        "$one = @([pscustomobject]@{ Exe = '/bin/echo'; Selector = '' }); "
        "foreach ($c in @($one)) { "
        "  if ($c.Exe -ne '/bin/echo') { Write-Output ('GOT: ' + $c); exit 1 } "
        "}; Write-Output 'ok'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_an_old_python_is_rejected_with_its_version_named(tmp_path):
    """And the reason is logged: 'install Python' was said to someone who had
    Python 3.14 installed, which is worse than saying nothing."""
    import sys

    if sys.platform == "win32":
        import pytest

        pytest.skip("this exercises the POSIX venv layout")
    result = _run_pwsh(f"New-Environment '{tmp_path / 'env'}' | Out-Null")
    combined = result.stdout + result.stderr
    assert "trying" in combined, f"every attempt must be logged:\n{combined}"


def test_python_is_looked_for_where_it_lives_not_only_on_path():
    """The failure: Desktop logged ...\\Programs\\Python\\Python314 in its own
    PATH, but the powershell.exe it spawned did not have it, so Get-Command
    found no Python at all. A per-user install writes the *user* PATH, and a
    child started from a differently-scoped environment does not see it."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "PythonCore" in text, "the registry records where Python installed itself"
    assert "Programs\\Python" in text, "and the standard per-user install folder"
    assert text.index("PythonCore") < text.index("Get-Command $name"), (
        "PATH must be the fallback, not the first thing tried"
    )


def test_windowsapps_stubs_are_ignored():
    """Those are zero-byte execution aliases that open the Microsoft Store."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "WindowsApps" in text and "return }" in text


def test_no_join_path_is_given_a_variable_that_may_be_unset():
    """Join-Path throws on a null path, and a stripped environment is exactly
    the case this code exists to survive — found by running it with env -i."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    for line_number, line in enumerate(text.splitlines(), 1):
        if "Join-Path $env:" not in line:
            continue
        variable = line.split("Join-Path $env:")[1].split()[0].strip("'\")}")
        guarded = f"if ($env:{variable})" in text or f"if (${{env:{variable}}})" in text
        assert guarded, f"line {line_number} uses $env:{variable} unguarded"


def test_the_path_it_actually_saw_is_logged_on_failure():
    """So the next failure arrives with the evidence instead of a guess."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "PATH seen by this process" in text


def test_pathext_is_repaired_before_anything_runs():
    """Every interpreter on the machine reported "cannot run a document in the
    middle of a pipeline" — a valid python.exe, the py launcher and uv alike.
    That is what PowerShell says when PATHEXT does not list the extension, and
    Claude Desktop starts this process with a stripped environment."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    assert "PATHEXT" in text
    assert text.index("PATHEXT") < text.index("function Invoke-Exe")


def test_nothing_is_run_through_a_pipeline():
    """The call operator consults PATHEXT and can ShellExecute a program as if
    it were a document. Start-Process with redirection is CreateProcess."""
    text = PS1.read_bytes()[len(BOM):].decode("utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for forbidden in ("& $py", "& $uv", "& $exe", "& $created", "& $server"):
        assert forbidden not in code, f"{forbidden} bypasses Invoke-Exe"
    assert "UseShellExecute = $false" in code, "the server must be CreateProcess too"


def test_arguments_containing_spaces_survive():
    """Start-Process -ArgumentList loses the grouping of any argument with a
    space. It silently broke the version probe, which then read every Python as
    "0.0" — and would have broken pip install, since the extension's own path
    contains one: "Claude Extensions"."""
    result = _run_pwsh(
        "Write-Output (Format-ExeArgs @('-c','import sys; print(1)')); "
        "Write-Output (Format-ExeArgs @('-m','pip','install','C:\\A B\\src'))"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    assert lines[0] == '-c "import sys; print(1)"', lines
    assert lines[1] == '-m pip install "C:\\A B\\src"', lines


def test_a_program_that_will_not_run_is_reported_not_assumed_missing():
    result = _run_pwsh(
        "$r = Invoke-Exe '/does/not/exist' @('x'); "
        "if ($r.Code -eq 0) { Write-Output 'WRONG'; exit 1 }; Write-Output 'reported'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reported" in result.stdout
