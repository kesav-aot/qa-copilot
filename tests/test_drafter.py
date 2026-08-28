"""The drafter must scaffold what it recognises and refuse to guess the rest.

A drafter that silently invented selectors would produce plans that look
reviewed when they are not, which is worse than producing nothing.
"""

from __future__ import annotations

import json

from qa_copilot.dsl.schema import TestPlan
from qa_copilot.ingest.drafter import draft_plan
from qa_copilot.ingest.models import ManualStep, ManualTestCase


def case(steps, **kwargs) -> ManualTestCase:
    body = {
        "id": "TC-1",
        "title": "A case",
        "source": "x.md",
        "format": "markdown",
        "steps": [ManualStep(number=i + 1, action=a) for i, a in enumerate(steps)],
        "expected_results": ['The page shows "Done"'],
    }
    body.update(kwargs)
    return ManualTestCase(**body)


def actions(draft) -> list[str]:
    return [s["action"] for s in draft["draft_plan"]["steps"]]


# --- step mapping ----------------------------------------------------------

def test_login_becomes_authenticate_with_no_credentials(copilot):
    draft = draft_plan(
        case(["Log in as an administrator"], preconditions=[]), "demo", copilot.broker
    )
    step = draft["draft_plan"]["steps"][0]
    assert step["action"] == "authenticate"
    assert step.get("capability") in {"manage_users", "manage_settings"}
    assert "password" not in json.dumps(draft["draft_plan"]).lower()


def test_a_logged_in_precondition_adds_the_authenticate_step(copilot):
    draft = draft_plan(
        case(["Navigate to the Users page"], preconditions=["The user is logged in as an admin"]),
        "demo",
        copilot.broker,
    )
    assert actions(draft)[0] == "authenticate"
    assert any("from the preconditions" in n for n in draft["notes"])


def test_login_is_not_added_twice(copilot):
    draft = draft_plan(
        case(["Log in as an admin", "Navigate to the Users page"],
             preconditions=["Logged in as an administrator"]),
        "demo",
        copilot.broker,
    )
    assert actions(draft).count("authenticate") == 1


def test_navigate_derives_a_path_and_flags_it_as_a_guess(copilot):
    draft = draft_plan(case(["Navigate to the User Management page"]), "demo", copilot.broker)
    navigate = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "navigate")
    assert navigate["path"] == "/user-management"
    assert any("confirm the page really is at" in t for t in draft["todos"])


def test_an_explicit_path_in_the_step_is_used_verbatim(copilot):
    draft = draft_plan(case(["Navigate to /api/orders"]), "demo", copilot.broker)
    navigate = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "navigate")
    assert navigate["path"] == "/api/orders"


def test_click_uses_the_quoted_label_and_warns_about_duplicates(copilot):
    """The TODO is read by a QA engineer, so it must not say 'data-testid'."""
    draft = draft_plan(case(['Click the "Disable" button']), "demo", copilot.broker)
    click = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "click")
    assert click["target"] == {"text": "Disable"}
    todo = next(t for t in draft["todos"] if "Disable" in t)
    assert "more than once" in todo and "data-testid" not in todo


def test_filling_a_credential_field_is_refused_and_explained(copilot):
    draft = draft_plan(
        case(["Enter the admin user in the username field",
              "Enter the secret in the password field"]),
        "demo",
        copilot.broker,
    )
    assert not any(s["action"] == "fill" and "password" in json.dumps(s).lower()
                   for s in draft["draft_plan"]["steps"])
    assert any("fill_secret" in t for t in draft["todos"])


def test_an_unmappable_step_becomes_a_todo_not_a_guess(copilot):
    draft = draft_plan(case(["Do the needful with the widget"]), "demo", copilot.broker)
    assert any("could not be mapped" in t for t in draft["todos"])
    assert "click" not in actions(draft)


# --- assertions ------------------------------------------------------------

def test_a_quoted_expectation_becomes_a_text_assertion(copilot):
    draft = draft_plan(case(['Verify the heading "User Management" is shown']), "demo", copilot.broker)
    assertion = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "assert")
    assert assertion == {"action": "assert", "kind": "text", "expected": "User Management"}


def test_an_apostrophe_is_not_mistaken_for_a_quote(copilot):
    draft = draft_plan(
        case(["Navigate to Home"], expected_results=["The user's status is \"disabled\""]),
        "demo",
        copilot.broker,
    )
    assertion = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "assert")
    assert assertion["expected"] == "disabled"


def test_a_url_expectation_becomes_a_url_assertion(copilot):
    draft = draft_plan(
        case(["Navigate to Home"], expected_results=["The URL contains /dashboard"]),
        "demo",
        copilot.broker,
    )
    assert {"action": "assert", "kind": "url_contains", "expected": "/dashboard"} in draft[
        "draft_plan"
    ]["steps"]


def test_a_status_expectation_becomes_a_status_assertion(copilot):
    draft = draft_plan(
        case(["Navigate to Home"], expected_results=["The response status is 403"]),
        "demo",
        copilot.broker,
    )
    assert {"action": "assert", "kind": "status", "expected": 403} in draft["draft_plan"]["steps"]


def test_prose_expectations_become_todos_rather_than_brittle_assertions(copilot):
    draft = draft_plan(
        case(["Navigate to Home"], expected_results=["Everything works as it should"]),
        "demo",
        copilot.broker,
    )
    assert any("needs an explicit assertion" in t for t in draft["todos"])


def test_a_duplicated_expectation_is_asserted_once(copilot):
    draft = draft_plan(
        case(['Verify the text "Done" is shown'], expected_results=['The page shows "Done"']),
        "demo",
        copilot.broker,
    )
    asserts = [s for s in draft["draft_plan"]["steps"] if s["action"] == "assert"]
    assert len(asserts) == 1
    assert any("duplicate" in n for n in draft["notes"])


def test_a_draft_with_no_assertions_says_so(copilot):
    draft = draft_plan(
        case(["Navigate to Home"], expected_results=[]), "demo", copilot.broker
    )
    assert any("no assertions" in t for t in draft["todos"])


# --- output contract -------------------------------------------------------

def test_the_draft_validates_against_the_real_schema(copilot):
    draft = draft_plan(
        case(['Navigate to the Users page', 'Verify the text "User Management" is shown'],
             preconditions=["Logged in as an administrator"]),
        "demo",
        copilot.broker,
    )
    assert draft["draft_is_valid"], draft["schema_errors"]
    TestPlan.model_validate(draft["draft_plan"])


def test_coverage_reflects_how_much_was_actually_mapped(copilot):
    draft = draft_plan(
        case(["Navigate to Home", "Do the needful", "Frobnicate the widget"]),
        "demo",
        copilot.broker,
    )
    assert draft["step_coverage_percent"] == 33


def test_a_case_containing_a_credential_is_refused_outright(copilot):
    draft = draft_plan(
        case(["Log in with password Hunter2Example"]), "demo", copilot.broker
    )
    assert draft["refused"] is True
    assert draft["draft_plan"] is None
    assert "Hunter2Example" not in json.dumps(draft)
    assert any("secret store" in step for step in draft["remediation"])


def test_every_draft_is_marked_as_needing_review(copilot):
    draft = draft_plan(case(["Navigate to Home"]), "demo", copilot.broker)
    assert draft["review_required"] is True
    assert "scaffold" in draft["guidance"]


# --- api steps -------------------------------------------------------------

def test_an_api_call_becomes_an_api_request_step(copilot):
    draft = draft_plan(
        case(["Call GET /api/users"], preconditions=["Logged in as an administrator"]),
        "demo",
        copilot.broker,
    )
    request = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "api_request")
    assert request["method"] == "GET" and request["path"] == "/api/users"
    assert request["identity"] == "ADMIN_USER"
    assert any("confirm 'ADMIN_USER'" in t for t in draft["todos"])


def test_a_bare_verb_and_path_is_recognised(copilot):
    draft = draft_plan(case(["DELETE /api/users/1"]), "demo", copilot.broker)
    request = next(s for s in draft["draft_plan"]["steps"] if s["action"] == "api_request")
    assert request["method"] == "DELETE" and request["path"] == "/api/users/1"


def test_an_api_step_without_a_known_identity_asks_for_one(copilot):
    draft = draft_plan(case(["Send a POST to /api/login"]), "demo", copilot.broker)
    assert any("set the identity" in t for t in draft["todos"])


def test_an_unknown_actor_produces_a_placeholder_and_a_todo(copilot):
    draft = draft_plan(
        case(["Log in and open Home"], preconditions=[]), "demo", copilot.broker
    )
    step = draft["draft_plan"]["steps"][0]
    assert step == {"action": "authenticate", "identity": "REPLACE_ME"}
    assert any("REPLACE_ME" in t for t in draft["todos"])
