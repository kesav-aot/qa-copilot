"""Evidence nobody had to ask for, and the report that carries it.

The request these tests stand for: a person who never wrote "take a screenshot"
still gets a picture of every step, and a report they can open — every time.
"""

from __future__ import annotations

import json
from pathlib import Path

from qa_copilot import mcp_server
from qa_copilot.htmlreport import build_html

SECRETS = ["Adm1n-Demo-Pass!", "Us3r-Demo-Pass!", "admin@qa.local", "user@qa.local"]

NO_SCREENSHOT_LINES = """# Admin can reach user management

Log in as an admin
Go to /users
Check the page shows "User Management"
"""

FAILING = """# The page says something it does not say

Log in as an admin
Go to /users
Check the page shows "This text is not on the page"
Check the page shows "User Management"
"""


async def test_every_step_is_photographed_without_being_asked(copilot):
    result = await copilot.run_plain(NO_SCREENSHOT_LINES)
    assert result["overall"] == "passed", result
    steps = result["results"][0]["report"]["steps"]
    assert len(steps) == 3
    for step in steps:
        assert Path(step["screenshot"]).is_file(), step


async def test_each_run_keeps_its_pictures_in_its_own_folder(copilot):
    first = await copilot.run_plain(NO_SCREENSHOT_LINES)
    second = await copilot.run_plain(NO_SCREENSHOT_LINES)
    folder = lambda r: Path(r["results"][0]["report"]["steps"][0]["screenshot"]).parent  # noqa: E731
    assert folder(first) != folder(second)
    assert len(list(folder(first).glob("*.png"))) == 3


async def test_key_steps_mode_skips_the_typing_and_clicking(copilot):
    copilot.config.evidence.screenshots = "key_steps"
    result = await copilot.run_plain(
        "# Search\n\nLog in as an admin\nGo to /users\nClick the Dashboard link\n"
        'Check the page shows "Dashboard"\n'
    )
    assert result["overall"] == "passed", result
    shots = [("screenshot" in s) for s in result["results"][0]["report"]["steps"]]
    assert shots == [True, True, False, True]


async def test_failures_only_mode_still_photographs_the_failure(copilot):
    copilot.config.evidence.screenshots = "failures_only"
    result = await copilot.run_plain(FAILING)
    steps = result["results"][0]["report"]["steps"]
    assert [("screenshot" in s) for s in steps] == [False, False, True]


async def test_a_report_is_written_after_a_run_nobody_asked_to_report(copilot):
    result = await copilot.run_plain(NO_SCREENSHOT_LINES)
    info = result["html_report"]
    path = Path(info["path"])
    assert path.is_file() and path.suffix == ".html"
    assert path.parent == copilot.config.reports_path
    html = path.read_text()
    assert "Admin can reach user management" in html
    assert html.count("data:image/png;base64,") == 3
    assert "Check the page shows" in html
    for secret in SECRETS:
        assert secret not in html


async def test_a_dsl_run_gets_a_report_too(copilot):
    import yaml

    plan = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "examples" / "admin-can-reach-user-management.yaml").read_text()
    )
    report = await copilot.run(plan)
    assert report["status"] == "passed"
    assert Path(report["html_report"]["path"]).is_file()


async def test_a_failed_run_reports_what_went_wrong_and_what_did_not_run(copilot):
    result = await copilot.run_plain(FAILING)
    assert result["overall"] == "failed"
    html = Path(result["html_report"]["path"]).read_text()
    assert "Step 3 went wrong" in html
    assert "1 later step did not run" in html
    assert "Failed" in html


async def test_a_blocked_run_writes_no_report_because_nothing_ran(copilot):
    import yaml

    plan = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "examples" / "disable-user.yaml").read_text()
    )
    blocked = await copilot.run(plan)
    assert blocked["status"] == "blocked"
    assert "html_report" not in blocked


async def test_reports_can_be_turned_off(copilot):
    copilot.config.report.auto = False
    result = await copilot.run_plain(NO_SCREENSHOT_LINES)
    assert "html_report" not in result


async def test_earlier_runs_can_be_listed_and_combined_into_one_report(copilot):
    one = await copilot.run_plain(NO_SCREENSHOT_LINES)
    two = await copilot.run_plain(FAILING)
    runs = copilot.list_reports()["runs"]
    assert [r["run_id"] for r in runs] == [
        two["html_report"]["run_id"],
        one["html_report"]["run_id"],
    ]
    assert runs[0]["counts"] == {"failed": 1}

    combined = copilot.generate_report(
        [one["html_report"]["run_id"], two["html_report"]["run_id"]], "Release 4.2 sign-off"
    )
    html = Path(combined["path"]).read_text()
    assert "Release 4.2 sign-off" in html
    assert "1 of 2 passed" in html
    assert "Admin can reach user management" in html
    assert "The page says something it does not say" in html


async def test_with_no_run_named_the_latest_one_is_reported(copilot):
    await copilot.run_plain(NO_SCREENSHOT_LINES)
    again = copilot.generate_report()
    assert Path(again["path"]).is_file()
    assert again["title"] == "Admin can reach user management"


def test_reporting_before_anything_ran_says_so(copilot):
    assert "nothing has been run" in copilot.generate_report()["error"]


def test_a_run_id_cannot_be_used_to_read_another_file(copilot):
    """The id comes from a model and names a file on disk."""
    for attempt in ["../../config/identities", "/etc/passwd", "20260101-000000-zzzzzz", ""]:
        out = copilot.generate_report([attempt])
        assert "error" in out, attempt


def test_text_from_the_application_cannot_put_markup_in_the_report():
    """A page title or an error message is whatever the app under test says."""
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    html = build_html(
        evil,
        [
            {
                "name": evil,
                "plan": {"steps": [{"action": "assert", "kind": "text", "expected": evil}]},
                "report": {
                    "status": "failed",
                    "plan": evil,
                    "steps_run": 1,
                    "steps_total": 1,
                    "steps": [{"index": 0, "action": "assert", "status": "failed", "detail": evil}],
                    "failure": {"step_index": 0, "detail": evil, "console_errors": [evil]},
                },
            }
        ],
    )
    assert "<script>alert(1)" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(1)" in html


async def test_the_tool_result_tells_the_model_to_hand_over_the_report(copilot, monkeypatch):
    monkeypatch.setattr(mcp_server, "copilot", lambda: copilot)
    opened: list[str] = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    out = json.loads(await mcp_server.run_plain_test(NO_SCREENSHOT_LINES))
    info = out["html_report"]
    assert info["opened_in_browser"] is False  # switched off for the test run
    assert opened == []
    assert info["path"] in info["tell_the_user"]

    monkeypatch.setenv("QA_COPILOT_OPEN_REPORTS", "1")
    out = json.loads(await mcp_server.run_plain_test(NO_SCREENSHOT_LINES))
    assert out["html_report"]["opened_in_browser"] is True
    assert opened == [out["html_report"]["link"]]
    assert "open in your browser" in out["html_report"]["tell_the_user"]


async def test_the_person_can_stop_reports_opening_themselves(copilot, monkeypatch):
    monkeypatch.setattr(mcp_server, "copilot", lambda: copilot)
    monkeypatch.setenv("QA_COPILOT_OPEN_REPORTS", "1")
    copilot.config.report.open = False
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: (_ for _ in ()).throw(AssertionError))
    out = json.loads(await mcp_server.run_plain_test(NO_SCREENSHOT_LINES))
    assert out["html_report"]["opened_in_browser"] is False
    assert Path(out["html_report"]["path"]).is_file()


def test_the_settings_file_is_actually_read(tmp_path):
    """A setting nobody wired into load_config is silently ignored."""
    from qa_copilot.config import load_config

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        "evidence:\n  screenshots: key_steps\nreport:\n  auto: false\n  open: false\n"
        "report_dir: elsewhere\nskill_dir: know-how\n"
    )
    config = load_config(config_dir)
    assert config.evidence.screenshots == "key_steps"
    assert config.report.auto is False and config.report.open is False
    assert config.reports_path == tmp_path / "elsewhere"
    assert config.skills_path == tmp_path / "know-how"
