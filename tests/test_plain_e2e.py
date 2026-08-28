"""Plain English, end to end, against the live demo app."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ["Adm1n-Demo-Pass!", "Us3r-Demo-Pass!", "admin@qa.local", "user@qa.local"]


def assert_no_secrets(payload) -> None:
    blob = json.dumps(payload, default=str)
    for secret in SECRETS:
        assert secret not in blob, f"{secret!r} leaked into a model-visible payload"


ADMIN_TEST = """# Admin can reach user management
Tags: smoke

Log in as an admin
Go to /users
Check the page shows "User Management"
Check I can see the Dashboard link
"""

BLOCKED_TEST = """# Standard user cannot manage users

Log in as a standard user
Go to /users
Verify "Access denied" is displayed
Check I should not see the Disable button
Call GET /api/users as a standard user, expecting 403
"""


async def test_a_plain_english_test_runs_and_passes(copilot):
    result = await copilot.run_plain(ADMIN_TEST)
    assert result["understood"] and result["ran"]
    assert result["overall"] == "passed", result
    assert_no_secrets(result)


async def test_the_api_capability_is_resolved_at_run_time(copilot):
    result = await copilot.run_plain(BLOCKED_TEST)
    assert result["overall"] == "passed", result
    steps = result["results"][0]["report"]["steps"]
    assert steps[-1]["detail"].endswith("-> 403")


async def test_two_tests_in_one_file_both_run(copilot):
    result = await copilot.run_plain(ADMIN_TEST + "\n" + BLOCKED_TEST)
    assert len(result["results"]) == 2
    assert result["counts"]["passed"] == 2


async def test_nothing_runs_if_any_line_is_not_understood(copilot):
    result = await copilot.run_plain(ADMIN_TEST + "\nFrobnicate the widget\n")
    assert result["understood"] is False
    assert result["ran"] is False
    assert result["results"] == []


async def test_a_destructive_plain_test_still_hits_the_approval_gate(copilot):
    text = """# Admin disables a user

Log in as an admin
Go to /users
Click Disable for Rae Rivera
Check the page shows "disabled"
"""
    result = await copilot.run_plain(text)
    report = result["results"][0]["report"]
    assert report["status"] == "blocked"
    assert "approve" in report["reason"]

    copilot.approve(report["policy"]["fingerprint"], approver="qa-lead")
    again = await copilot.run_plain(text)
    assert again["overall"] == "passed", again


async def test_an_ambiguous_step_fails_with_the_candidates_listed(copilot):
    text = """# Ambiguous

Log in as an admin
Go to /users
Click Disable
Check the page shows "disabled"
"""
    compiled = copilot.compile_plain(text)
    copilot.approve(compiled["tests"][0]["policy"]["fingerprint"], approver="qa-lead")

    result = await copilot.run_plain(text)
    detail = result["results"][0]["report"]["failure"]["detail"]
    assert "matches 2 things" in detail
    assert "Rae Rivera" in detail and "Kit Osei" in detail
    assert "Narrow it down" in detail


async def test_a_missing_element_fails_with_the_page_contents(copilot):
    text = """# Missing element

Log in as an admin
Go to /users
Click the Export button
Check the page shows "Exported"
"""
    result = await copilot.run_plain(text)
    detail = result["results"][0]["report"]["failure"]["detail"]
    assert "could not find" in detail
    assert "Dashboard" in detail, "the error must list what is actually there"


async def test_the_compile_step_warns_about_approval_before_you_run(copilot):
    compiled = copilot.compile_plain(
        "# Disable someone\n\nLog in as an admin\nGo to /users\n"
        'Click Disable for Rae Rivera\nCheck the page shows "disabled"\n'
    )
    test = compiled["tests"][0]
    assert test["policy"]["risk"] == "medium"
    assert any("approve" in note for note in test["notes"])


async def test_a_password_in_a_plain_test_is_refused_before_running(copilot):
    result = await copilot.run_plain("# T\n\nLog in with password Hunter2Example\n")
    assert not result["understood"] and not result["ran"]
    assert "Hunter2Example" not in json.dumps(result)


def test_the_bundled_english_tests_all_compile(copilot):
    files = sorted((ROOT / "english-tests").glob("*.txt"))
    assert files, "expected sample plain-English tests in english-tests/"
    for path in files:
        compiled = copilot.compile_plain(path.read_text(), name=path.stem)
        assert compiled["understood"], (path.name, compiled["tests"][0]["problems"])


async def test_the_bundled_english_tests_all_pass(copilot):
    for path in sorted((ROOT / "english-tests").glob("*.txt")):
        result = await copilot.run_plain(path.read_text(), name=path.stem)
        assert result["overall"] == "passed", (path.name, result)


async def test_a_failed_visibility_check_keeps_the_resolver_diagnostic(copilot):
    """The most useful error in the tool must survive being wrapped in an
    assertion — otherwise a check just says "not visible" and teaches nothing."""
    text = """# Wrong kind of thing

Log in as an admin
Go to /users
Check I can see the Users heading
"""
    result = await copilot.run_plain(text)
    detail = result["results"][0]["report"]["failure"]["detail"]
    assert "I expected to see" in detail
    assert "you asked for a heading" in detail
    assert "(link)" in detail, "tag names must be rendered as ordinary words"


async def test_checking_for_absence_passes_when_the_element_is_missing(copilot):
    text = """# Absence

Log in as a standard user
Go to /users
Check I should not see the Disable button
"""
    result = await copilot.run_plain(text)
    assert result["overall"] == "passed", result
    assert "absent" in result["results"][0]["report"]["steps"][-1]["detail"]


async def test_checking_for_absence_fails_when_the_element_is_there(copilot):
    text = """# Absence that is not

Log in as an admin
Go to /users
Check I should not see the User Management heading
"""
    result = await copilot.run_plain(text)
    detail = result["results"][0]["report"]["failure"]["detail"]
    assert "should not be on the page, but it is" in detail
