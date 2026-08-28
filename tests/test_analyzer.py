"""The analyser is the deterministic half of test-case triage. Its answers must
not drift, so they are pinned here."""

from __future__ import annotations

import pytest

from qa_copilot.ingest.analyzer import analyze
from qa_copilot.ingest.models import ManualStep, ManualTestCase


def case(**kwargs) -> ManualTestCase:
    body = {
        "id": "TC-1",
        "title": "A case",
        "source": "x.md",
        "format": "markdown",
        "steps": [ManualStep(number=1, action="Navigate to the Home page")],
        "expected_results": ['The page shows "Home"'],
    }
    body.update(kwargs)
    return ManualTestCase(**body)


def kinds(analysis) -> set[str]:
    return {f.kind for f in analysis.findings}


def messages(analysis) -> str:
    return " | ".join(f.message for f in analysis.findings)


# --- blockers --------------------------------------------------------------

def test_a_case_with_no_steps_is_not_automatable():
    result = analyze(case(steps=[]))
    assert not result.automatable
    assert "no steps" in messages(result)


def test_a_case_with_no_expected_result_is_not_automatable():
    result = analyze(case(expected_results=[]))
    assert not result.automatable
    assert "assert nothing" in messages(result)


def test_a_step_level_expectation_satisfies_the_expected_result_check():
    result = analyze(
        case(
            expected_results=[],
            steps=[ManualStep(number=1, action="Open Home", expected="Home is shown")],
        )
    )
    assert result.automatable


# --- credentials -----------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Log in with password Hunter2Example",
        "password: Adm1n-Demo-Pass",
        "Use token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.sig",
        "key sk-live_abcdefghijklmnopqrst",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_a_literal_credential_is_a_blocker(text):
    result = analyze(case(steps=[ManualStep(number=1, action=text)]))
    assert "security" in kinds(result)
    assert not result.automatable


@pytest.mark.parametrize(
    "text",
    [
        "Enter a valid password",
        "Leave the password field blank",
        "Verify the password error is shown",
    ],
)
def test_talking_about_passwords_is_not_a_credential(text):
    result = analyze(case(steps=[ManualStep(number=1, action=text)]))
    assert "security" not in kinds(result)


# --- ambiguity -------------------------------------------------------------

def test_vague_wording_is_flagged_with_the_question_to_ask():
    result = analyze(case(steps=[ManualStep(number=1, action="Enter appropriate data")]))
    finding = next(f for f in result.findings if "appropriate" in f.message)
    assert finding.suggestion == "appropriate by what rule?"
    assert finding.location == "step 1"


def test_a_vague_term_inside_a_domain_is_not_flagged():
    result = analyze(case(steps=[ManualStep(number=1, action="Email qa@example.invalid")]))
    assert not any("invalid" in f.message for f in result.findings)


def test_a_conditional_step_is_flagged_as_more_than_one_path():
    result = analyze(
        case(steps=[ManualStep(number=1, action="If the banner shows, dismiss it")])
    )
    assert any("conditional" in f.message for f in result.findings)


# --- data and environment --------------------------------------------------

def test_a_dependency_on_pre_existing_data_is_surfaced():
    result = analyze(case(preconditions=["An existing active order is present"]))
    assert "data" in kinds(result)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Check the confirmation email", "email"),
        ("Enter the OTP from the SMS", "SMS/OTP"),
        ("Solve the CAPTCHA", "CAPTCHA"),
        ("Complete payment via Stripe", "payment provider"),
    ],
)
def test_dependencies_a_browser_cannot_observe_are_surfaced(text, expected):
    result = analyze(case(steps=[ManualStep(number=1, action=text)]))
    assert any(expected in f.message for f in result.findings)


# --- risk ------------------------------------------------------------------

def test_a_destructive_case_is_medium_risk():
    result = analyze(case(steps=[ManualStep(number=1, action="Delete the record")]))
    assert result.inferred_risk == "medium"


def test_a_read_only_case_stays_low_risk():
    assert analyze(case()).inferred_risk == "low"


# --- actor selection -------------------------------------------------------

def test_an_admin_case_maps_to_an_admin_capability(copilot):
    result = analyze(
        case(preconditions=["Logged in as an administrator"]), copilot.broker, "demo"
    )
    assert result.suggested_capability in {"manage_users", "manage_settings"}
    assert result.suggested_identity == "ADMIN_USER"


def test_a_customer_case_maps_to_a_customer_capability(copilot):
    result = analyze(
        case(preconditions=["Logged in as a customer placing an order"]),
        copilot.broker,
        "demo",
    )
    assert result.suggested_capability == "create_order"
    assert result.suggested_identity == "STANDARD_USER"


def test_a_role_no_identity_provides_is_reported_with_what_is_available():
    """An admin case against a workspace that has only a read-only identity."""
    from qa_copilot.config import Config, Identity
    from qa_copilot.identity.broker import IdentityBroker
    from qa_copilot.secrets.env import EnvSecretProvider

    config = Config(
        identities={
            "VIEWER": Identity(alias="VIEWER", capabilities=["browse"], environments=["demo"])
        }
    )
    broker = IdentityBroker(config, EnvSecretProvider())

    result = analyze(case(preconditions=["Logged in as an administrator"]), broker, "demo")
    assert result.suggested_capability is None
    finding = next(f for f in result.findings if "no configured identity" in f.message)
    assert "browse" in (finding.suggestion or "")


def test_a_case_with_no_role_asks_who_performs_it(copilot):
    result = analyze(case(preconditions=["The system is available"]), copilot.broker, "demo")
    assert any("does not say who performs it" in f.message for f in result.findings)


# --- actor position --------------------------------------------------------

def test_a_role_named_as_an_object_is_not_taken_for_the_actor(copilot):
    """'Customer cannot open the admin settings page' is about the customer."""
    result = analyze(
        case(
            title="Customer cannot open admin settings",
            preconditions=["The customer is logged in"],
            steps=[ManualStep(number=1, action="Navigate to the admin settings page")],
            expected_results=['The page shows "Access denied"'],
        ),
        copilot.broker,
        "demo",
    )
    assert result.suggested_identity == "STANDARD_USER"


def test_a_negative_case_ignores_the_permission_it_is_denying(copilot):
    result = analyze(
        case(
            title="Standard user cannot manage users",
            preconditions=["Logged in as a standard user"],
            steps=[ManualStep(number=1, action="Navigate to the Users page")],
            expected_results=['The page shows "Access denied"'],
        ),
        copilot.broker,
        "demo",
    )
    assert result.suggested_capability == "browse"
    assert result.suggested_identity == "STANDARD_USER"
    assert any("negative test" in f.message for f in result.findings)


def test_an_action_hint_still_applies_to_a_positive_case_with_no_named_role(copilot):
    result = analyze(
        case(
            title="Settings page loads",
            preconditions=[],
            steps=[ManualStep(number=1, action="Open the settings page")],
            expected_results=['The page shows "Settings"'],
        ),
        copilot.broker,
        "demo",
    )
    assert result.suggested_capability == "manage_settings"
