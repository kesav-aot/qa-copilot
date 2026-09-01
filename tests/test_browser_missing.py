"""A browser that was never downloaded must not surface as a shell command.

The bug: the launcher decided whether to fetch Chromium by asking only whether
the cache *directory* existed. Playwright pins one build per version, so a
directory left by a different version — or by an interrupted download — holds
nothing this code can launch, and the download was then skipped forever. Every
test failed with Playwright's own message telling the reader to run
`playwright install`, which is the one thing the people this is built for cannot
be asked to do.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qa_copilot.executor.browser import is_browser_missing, open_session
from qa_copilot.executor.runner import ExecutionError

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "packaging" / "launcher" / "qa-copilot-launch"
LAUNCHER_PS1 = ROOT / "packaging" / "launcher" / "qa-copilot-launch.ps1"


def test_playwrights_missing_executable_is_recognised():
    assert is_browser_missing(Exception("Executable doesn't exist at /x/chromium-1234/chrome"))
    assert is_browser_missing(Exception("please run: playwright install"))


def test_a_real_fault_is_not_mistaken_for_a_missing_browser():
    assert not is_browser_missing(Exception("Target page, context or browser has been closed"))
    assert not is_browser_missing(Exception("net::ERR_CONNECTION_REFUSED"))


async def test_a_missing_browser_is_explained_in_plain_language(monkeypatch, tmp_path):
    from qa_copilot.config import Environment

    class _Launcher:
        async def launch(self, **_kw):
            raise RuntimeError("Executable doesn't exist at /x/chromium-1234/chrome")

    class _PW:
        chromium = _Launcher()

        async def stop(self):
            return None

    async def fake_start():
        return _PW()

    import playwright.async_api as api

    monkeypatch.setattr(api, "async_playwright", lambda: type("S", (), {"start": staticmethod(fake_start)})())
    monkeypatch.setattr("qa_copilot.executor.browser.start_browser_download", lambda: True)

    with pytest.raises(ExecutionError) as exc:
        await open_session(Environment(name="x", base_url="http://x"), tmp_path)

    message = str(exc.value)
    assert "playwright install" not in message, (
        "a QA engineer cannot be told to run a terminal command"
    )
    assert "browser" in message.lower() and "download" in message.lower()


def test_the_launcher_does_not_decide_from_the_cache_directory():
    """The regression itself. `[ ! -d $BROWSERS ]` is what caused this."""
    text = LAUNCHER.read_text()
    install = [ln for ln in text.splitlines() if "playwright install" in ln and not ln.strip().startswith("#")]
    assert install, "the launcher must fetch the browser"
    assert not re.search(r"if \[ ! -d \"\$BROWSERS\"", text), (
        "the download must not be skipped just because the directory exists"
    )


def test_the_launcher_keeps_the_download_log():
    """Sent to /dev/null, a failed download leaves no evidence at all."""
    text = LAUNCHER.read_text()
    assert "browser-install.log" in text
    assert "playwright install chromium >/dev/null" not in text


def test_both_launchers_agree():
    assert "playwright install chromium" in LAUNCHER.read_text()
    assert "playwright" in LAUNCHER_PS1.read_text()
    assert "browser-install.log" in LAUNCHER_PS1.read_text()


# --- the crash Sonal's Desktop log showed -----------------------------------
# Two servers started at once on an upgrade, both ran `uv pip install` into the
# same virtualenv, and one reached `exec` while the console script was being
# rewritten. Exec-ing a missing file exits the shell silently, so the host could
# only report "the process exited early" with no cause at all.


def test_provisioning_is_serialised():
    text = LAUNCHER.read_text()
    assert "setup.lock" in text, "two launchers must not install at once"
    assert "mkdir \"$LOCK\"" in text, "the lock must be taken atomically"
    assert "rmdir" in text, "and released"


def test_a_stale_lock_does_not_block_forever():
    assert "-mmin +10" in LAUNCHER.read_text(), "a lock from a dead process must expire"


def test_the_launcher_never_execs_into_nothing():
    """The silent failure. Without this the host says only 'exited early'."""
    text = LAUNCHER.read_text()
    exec_line = next(ln for ln in text.splitlines() if ln.startswith("exec "))
    assert '"$SERVER"' in exec_line
    assert "-x \"$SERVER\"" in text, "the server must be checked before exec"
    assert "the server is missing at" in text, "and the reason must reach the host"


def test_the_browser_is_verified_once_per_version_not_every_start():
    """Every start paying for a 500 MB check is what made the upgrade fragile."""
    text = LAUNCHER.read_text()
    assert "browser-verified-$VERSION" in text
    assert "nohup" in text, "the download must outlive the shell, not share its fate"


def test_both_launchers_have_the_same_protections():
    ps1 = LAUNCHER_PS1.read_text()
    assert "setup.lock" in ps1
    assert "browser-verified-" in ps1
    assert "the server is missing at" in ps1
