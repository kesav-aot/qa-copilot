"""A plan rendered to English must compile back to the same plan.

That round-trip is what lets the whole pipeline hand a QA engineer something
they can read, edit and re-run — including plans a model wrote.
"""

from __future__ import annotations

import pytest

from qa_copilot.plain import Context, compile_text, to_plain_english

CTX = Context(
    identities=["ADMIN_USER", "STANDARD_USER"],
    capabilities=["browse", "manage_users"],
    environments=["demo"],
    default_environment="demo",
)

PLAN = {
    "version": 1,
    "name": "A round trip",
    "environment": "demo",
    "tags": ["smoke"],
    "steps": [
        {"action": "authenticate", "identity": "ADMIN_USER"},
        {"action": "navigate", "path": "/users"},
        {"action": "click", "target": {"describe": "Disable", "within": "Rae Rivera"}},
        {"action": "fill", "target": {"describe": "the Search box"}, "value": "widget"},
        {"action": "select", "target": {"describe": "the Plan dropdown"}, "option": "Premium"},
        {"action": "wait_for", "target": {"describe": "Done"}, "timeout_ms": 15000},
        {"action": "pause", "seconds": 2},
        {"action": "screenshot", "name": "after"},
        {"action": "api_request", "method": "GET", "path": "/api/users",
         "identity": "ADMIN_USER", "expect_status": 200},
        {"action": "assert", "kind": "text", "expected": "User Management"},
        {"action": "assert", "kind": "url_contains", "expected": "/users"},
        {"action": "assert", "kind": "status", "expected": 200},
        {"action": "assert", "kind": "visible", "target": {"describe": "the Save button"}},
        {"action": "assert", "kind": "not_visible", "target": {"describe": "the Delete button"}},
    ],
}


def test_a_plan_rendered_to_english_compiles_back_to_itself():
    english = to_plain_english(PLAN)
    result = compile_text(english, CTX)
    assert result.ok, [p.render() for p in result.problems]
    assert result.tests[0].plan["steps"] == PLAN["steps"]


def test_the_headers_survive_the_round_trip():
    plan = compile_text(to_plain_english(PLAN), CTX).tests[0].plan
    assert plan["name"] == "A round trip"
    assert plan["environment"] == "demo"
    assert plan["tags"] == ["smoke"]


def test_a_capability_login_round_trips():
    plan = {**PLAN, "steps": [
        {"action": "authenticate", "capability": "manage_users"},
        {"action": "assert", "kind": "text", "expected": "x"},
    ]}
    assert compile_text(to_plain_english(plan), CTX).tests[0].plan["steps"] == plan["steps"]


def test_precise_targets_round_trip_via_their_test_id():
    plan = {**PLAN, "steps": [
        {"action": "click", "target": {"testid": "disable-user-1"}},
        {"action": "assert", "kind": "text", "expected": "x"},
    ]}
    english = to_plain_english(plan)
    assert "Click disable-user-1" in english
    # It becomes a describe target, which the resolver tries as a test id first.
    step = compile_text(english, CTX).tests[0].plan["steps"][0]
    assert step["target"] == {"describe": "disable-user-1"}


def test_todos_are_written_as_comments_so_they_do_not_break_the_file():
    english = to_plain_english(PLAN, todos=["check the path", "check the wording"])
    assert "// Before you rely on this test" in english
    assert compile_text(english, CTX).ok


def test_a_step_with_no_english_form_is_left_out_and_declared():
    plan = {**PLAN, "steps": [
        {"action": "fill_secret", "target": {"testid": "pw"},
         "secret_ref": "secret://demo/admin/password"},
        {"action": "assert", "kind": "text", "expected": "x"},
    ]}
    english = to_plain_english(plan)
    assert "fill_secret" in english and "left out" in english
    assert "secret://" not in english.split("//")[0]


@pytest.mark.parametrize("action", [
    "authenticate", "navigate", "click", "fill", "select", "wait_for",
    "pause", "screenshot", "api_request", "assert",
])
def test_every_action_has_an_english_form(action):
    from qa_copilot.plain.writer import step_to_english

    sample = next(s for s in PLAN["steps"] if s["action"] == action)
    assert step_to_english(sample), f"{action} has no plain-English form"
