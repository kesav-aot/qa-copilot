"""MVP 2 end to end: a manual test case becomes a saved, runnable plan.

These run against the live demo app, so they prove the whole pipeline rather
than just the parts.
"""

from __future__ import annotations

import json

SECRETS = ["Adm1n-Demo-Pass!", "Us3r-Demo-Pass!", "admin@qa.local", "user@qa.local"]


def assert_no_secrets(payload) -> None:
    blob = json.dumps(payload, default=str)
    for secret in SECRETS:
        assert secret not in blob, f"{secret!r} leaked into a model-visible payload"


# --- ingestion -------------------------------------------------------------

def test_ingest_reads_every_bundled_format(copilot):
    result = copilot.ingest_test_cases()
    assert result["errors"] == []
    assert len(result["files_read"]) >= 4
    ids = {c["id"] for c in result["cases"]}
    assert {"TC-1001", "TC-1002", "QA-4417", "TC-2001"} <= ids


def test_ingest_never_echoes_a_credential_it_found(copilot):
    result = copilot.ingest_test_cases()
    bad = next(c for c in result["cases"] if c["id"] == "TC-9001")
    assert not bad["analysis"]["automatable"]
    assert any("credential" in b for b in bad["blockers"])
    assert "Hunter2Example" not in json.dumps(result)


def test_ingest_can_be_scoped_to_one_file(copilot):
    result = copilot.ingest_test_cases("user-management.md")
    assert len(result["files_read"]) == 1
    assert {c["id"] for c in result["cases"]} == {"TC-1001", "TC-1002", "TC-1003"}


def test_ingest_refuses_a_path_outside_the_testcase_directory(copilot):
    result = copilot.ingest_test_cases("../config/identities.yaml")
    assert "outside the test-case directory" in result["error"]


def test_analyze_reports_a_missing_case_with_what_exists(copilot):
    result = copilot.analyze_test_case("TC-DOES-NOT-EXIST")
    assert "no test case with id" in result["error"]
    assert "TC-1001" in result["error"]


# --- drafting --------------------------------------------------------------

def test_draft_rejects_an_unknown_environment(copilot):
    result = copilot.draft_plan_from_test_case("TC-1001", "mars")
    assert "unknown environment" in result["error"]


def test_a_drafted_plan_validates_and_carries_no_credentials(copilot):
    draft = copilot.draft_plan_from_test_case("TC-1001", "demo")
    assert draft["draft_is_valid"], draft["schema_errors"]
    assert_no_secrets(draft)
    validation = copilot.validate(draft["draft_plan"])
    assert validation["valid"], validation


# --- the whole pipeline ----------------------------------------------------

async def test_manual_case_to_passing_run(copilot):
    """TC-1001 → draft → save → validate → run, with no human edits needed."""
    draft = copilot.draft_plan_from_test_case("TC-1001", "demo")
    plan = draft["draft_plan"]

    saved = copilot.save_plan(plan)
    assert saved["saved"] and saved["slug"] == "admin-can-view-the-user-management-page"

    listed = {p["slug"] for p in copilot.list_plans()["plans"]}
    assert saved["slug"] in listed

    report = await copilot.run(copilot.get_plan(saved["slug"])["plan"])
    assert report["status"] == "passed", report
    assert_no_secrets(report)


async def test_a_drafted_destructive_case_still_hits_the_approval_gate(copilot):
    draft = copilot.draft_plan_from_test_case("TC-1002", "demo")
    assert draft["draft_plan"]["risk"] == "medium"

    blocked = await copilot.run(draft["draft_plan"])
    assert blocked["status"] == "blocked"
    assert "approve" in blocked["reason"]


def test_saving_an_invalid_plan_reports_why(copilot):
    result = copilot.save_plan({"version": 1, "name": "x", "environment": "demo", "steps": []})
    assert not result["saved"] and result["errors"]


def test_saving_does_not_approve(copilot):
    draft = copilot.draft_plan_from_test_case("TC-1002", "demo")
    saved = copilot.save_plan(draft["draft_plan"])
    assert saved["policy"]["requires_approval"] is True
    assert saved["policy"]["approved"] is False


# --- suites ----------------------------------------------------------------

async def test_a_suite_runs_every_plan_and_aggregates(copilot):
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1001", "demo")["draft_plan"])
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1003", "demo")["draft_plan"])
    slugs = [p["slug"] for p in copilot.list_plans()["plans"]]

    result = await copilot.run_suite(plans=slugs)
    assert result["plans_run"] == 2
    assert result["counts"]["passed"] == 2
    assert result["overall"] == "passed"
    assert_no_secrets(result)


async def test_a_blocked_plan_does_not_stop_the_rest_of_the_suite(copilot):
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1001", "demo")["draft_plan"])
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1002", "demo")["draft_plan"])
    slugs = sorted(p["slug"] for p in copilot.list_plans()["plans"])

    result = await copilot.run_suite(plans=slugs)
    assert result["counts"] == {
        "passed": 1, "failed": 0, "error": 0, "blocked": 1, "invalid": 0
    }
    assert result["overall"] == "failed", "a blocked plan must not read as a pass"


async def test_stop_on_failure_halts_the_suite(copilot):
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1002", "demo")["draft_plan"])
    copilot.save_plan(copilot.draft_plan_from_test_case("TC-1001", "demo")["draft_plan"])
    slugs = sorted(p["slug"] for p in copilot.list_plans()["plans"])

    result = await copilot.run_suite(plans=slugs, stop_on_failure=True)
    assert result["plans_run"] == 1
    assert result["plans_requested"] == 2


async def test_an_unknown_suite_is_an_error_not_a_pass(copilot):
    result = await copilot.run_suite(suite="does-not-exist")
    assert "no suite named" in result["error"]


async def test_a_suite_with_nothing_in_it_is_an_error(copilot):
    result = await copilot.run_suite(plans=[])
    assert "error" in result
