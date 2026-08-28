"""The claim this project has to make good on: a real browser login runs, and no
credential appears anywhere the model can see."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ["Adm1n-Demo-Pass!", "Us3r-Demo-Pass!", "admin@qa.local", "user@qa.local"]


def load_example(name: str) -> dict:
    return yaml.safe_load((ROOT / "examples" / name).read_text())


def assert_no_secrets(payload) -> None:
    blob = json.dumps(payload, default=str)
    for secret in SECRETS:
        assert secret not in blob, f"{secret!r} leaked into a model-visible payload"


async def test_admin_login_and_user_management(copilot):
    plan = load_example("admin-can-reach-user-management.yaml")

    validation = copilot.validate(plan)
    assert validation["valid"], validation
    assert validation["policy"]["can_execute"], validation
    assert validation["unresolved_identities"] == []

    report = await copilot.run(plan)
    assert report["status"] == "passed", report
    assert report["steps"][0]["detail"].startswith("authenticated as ADMIN_USER")
    assert_no_secrets(report)
    assert report["artifacts"], "expected a screenshot artifact"
    assert Path(report["artifacts"][0]).is_file()


async def test_authorization_boundary_by_capability(copilot):
    report = await copilot.run(load_example("standard-user-blocked-from-admin.yaml"))
    assert report["status"] == "passed", report
    assert "STANDARD_USER" in report["steps"][0]["detail"]
    assert_no_secrets(report)


async def test_destructive_plan_is_blocked_until_a_human_approves(copilot):
    plan = load_example("disable-user.yaml")

    blocked = await copilot.run(plan)
    assert blocked["status"] == "blocked"
    assert blocked["policy"]["risk"] == "medium"
    assert "approve" in blocked["reason"]

    copilot.approve(blocked["policy"]["fingerprint"], approver="qa-lead", note="reviewed")
    passed = await copilot.run(plan)
    assert passed["status"] == "passed", passed
    assert_no_secrets(passed)


async def test_failure_report_carries_evidence_but_no_credentials(copilot):
    plan = {
        "version": 1,
        "name": "deliberate failure",
        "environment": "demo",
        "steps": [
            {"action": "authenticate", "identity": "ADMIN_USER"},
            {"action": "assert", "kind": "visible", "target": {"testid": "does-not-exist"}},
        ],
    }
    report = await copilot.run(plan)
    assert report["status"] == "failed"
    assert report["failure"]["step_index"] == 1
    assert "screenshot" in report["failure"]
    assert report["failure"]["page"]["url"].endswith("/dashboard")
    assert_no_secrets(report)


async def test_wrong_credentials_fail_closed_without_echoing_them(copilot, monkeypatch):
    monkeypatch.setenv("QA_SECRET__DEMO__ADMIN__PASSWORD", "definitely-the-wrong-password")
    copilot.provider = type(copilot.provider)()
    copilot.broker.provider = copilot.provider

    report = await copilot.run(load_example("admin-can-reach-user-management.yaml"))
    assert report["status"] == "failed"
    assert "authentication as ADMIN_USER failed" in report["failure"]["detail"]
    assert "definitely-the-wrong-password" not in json.dumps(report)


async def test_audit_log_records_the_alias_and_never_the_value(copilot):
    await copilot.run(load_example("admin-can-reach-user-management.yaml"))
    entries = copilot.audit.tail(50)
    events = [e["event"] for e in entries]
    assert "auth.attempt" in events and "plan.finish" in events
    auth = next(e for e in entries if e["event"] == "auth.attempt")
    assert auth["identity"] == "ADMIN_USER"
    assert_no_secrets(entries)


async def test_screenshot_masks_the_password_field(copilot):
    """A screenshot taken on the login page must not render typed characters.

    Playwright masks with a solid overlay; we assert the mask locators were
    registered, which is what drives it.
    """
    from qa_copilot.executor.browser import open_session

    env = copilot.config.environment("demo")
    session = await open_session(env, Path(copilot.config.artifact_dir))
    try:
        creds = copilot.broker.credentials(copilot.config.identity("ADMIN_USER"))
        await session.login(env.login, creds)
        assert len(session._secret_targets) == 2
        shot = await session.screenshot("masked")
        assert Path(shot).is_file()
    finally:
        await session.close()
