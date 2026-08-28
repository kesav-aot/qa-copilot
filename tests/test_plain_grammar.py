"""The phrasebook is a promise. Every example in it must parse."""

from __future__ import annotations

import pytest

from qa_copilot.plain import Context, compile_line, phrasebook
from qa_copilot.plain.grammar import RULES

CTX = Context(
    identities=["ADMIN_USER", "STANDARD_USER"],
    capabilities=["browse", "create_order", "manage_settings", "manage_users"],
    environments=["demo"],
    default_environment="demo",
)

ALL_EXAMPLES = [(rule.name, ex) for rule in RULES for ex in rule.examples]


@pytest.mark.parametrize(("rule_name", "example"), ALL_EXAMPLES, ids=[e for _, e in ALL_EXAMPLES])
def test_every_documented_example_parses(rule_name, example):
    built = compile_line(example, CTX)
    assert built is not None, f"{example!r} matches no rule"
    assert built.error is None, f"{example!r} → {built.error}"
    assert built.explain, f"{example!r} produced no explanation"


def test_the_phrasebook_example_file_compiles():
    from qa_copilot.plain import compile_text

    result = compile_text(phrasebook()["example_file"], CTX)
    assert result.ok, [p.render() for p in result.problems]


def steps(text: str):
    built = compile_line(text, CTX)
    assert built is not None and built.error is None, text
    return built.steps


# --- signing in -------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    ["Log in as an admin", "log in as an administrator", "Sign in as an Admin",
     "Sign-in as an admin", "Log in to the app as an admin"],
)
def test_admin_phrasings_all_work(text):
    assert steps(text)[0]["capability"] in {"manage_users", "manage_settings"}


def test_an_exact_alias_is_used_directly():
    assert steps("Log in as ADMIN_USER") == [{"action": "authenticate", "identity": "ADMIN_USER"}]


def test_an_alias_written_with_spaces_still_matches():
    assert steps("Log in as standard user")[0]["identity"] == "STANDARD_USER"


def test_a_capability_can_be_asked_for_directly():
    assert steps("Log in as someone who can manage settings") == [
        {"action": "authenticate", "capability": "manage_settings"}
    ]


def test_an_unknown_person_lists_the_accounts_that_exist():
    built = compile_line("Log in as a wizard", CTX)
    assert built.error and "ADMIN_USER" in built.error


def test_an_unknown_capability_lists_the_ones_that_exist():
    built = compile_line("Log in as someone who can fly", CTX)
    assert built.error and "manage users" in built.error


def test_login_without_saying_who_is_rejected_helpfully():
    built = compile_line("Log in", CTX)
    assert built.error and "who" in built.error


# --- navigation -------------------------------------------------------------

def test_an_explicit_path_is_used_as_written():
    built = compile_line("Go to /users", CTX)
    assert built.steps == [{"action": "navigate", "path": "/users"}]
    assert built.warning is None


def test_a_page_name_is_turned_into_a_path_and_flagged():
    built = compile_line("Open the User Management page", CTX)
    assert built.steps[0]["path"] == "/user-management"
    assert "worked out" in built.warning


def test_a_full_url_is_kept():
    assert steps("Visit https://example.com/help")[0]["path"] == "https://example.com/help"


# --- clicking ---------------------------------------------------------------

def test_click_uses_a_late_bound_description():
    assert steps("Click the Save button") == [
        {"action": "click", "target": {"describe": "the Save button"}}
    ]


def test_click_in_a_row_scopes_the_search():
    assert steps("Click Disable for Rae Rivera")[0]["target"] == {
        "describe": "Disable", "within": "Rae Rivera"
    }


def test_click_in_the_named_row_scopes_the_search():
    assert steps('Click "Edit" in the Kit Osei row')[0]["target"] == {
        "describe": "Edit", "within": "Kit Osei"
    }


@pytest.mark.parametrize("verb", ["Click", "Press", "Tap", "Hit"])
def test_click_synonyms(verb):
    assert steps(f"{verb} Save")[0]["action"] == "click"


# --- forms ------------------------------------------------------------------

def test_typing_into_a_field():
    assert steps('Type "blue widget" into the Search box') == [
        {"action": "fill", "target": {"describe": "the Search box"}, "value": "blue widget"}
    ]


def test_fill_with_names_the_field_first():
    assert steps("Fill in Email with someone@example.com") == [
        {"action": "fill", "target": {"describe": "Email"}, "value": "someone@example.com"}
    ]


def test_typing_a_password_is_refused_with_the_alternative():
    built = compile_line('Type "hunter2" into the Password field', CTX)
    assert built.error and "credential" in built.error
    assert "log in as" in built.suggestion


def test_selecting_from_a_dropdown():
    assert steps('Select "Premium" from the Plan dropdown') == [
        {"action": "select", "target": {"describe": "the Plan dropdown"}, "option": "Premium"}
    ]


def test_ticking_a_checkbox_is_a_click_not_an_assertion():
    assert steps('Tick "Remember me"')[0]["action"] == "click"
    assert steps('Check the "Remember me" checkbox')[0]["action"] == "click"


# --- checking ---------------------------------------------------------------

def test_check_page_shows():
    assert steps('Check the page shows "User Management"') == [
        {"action": "assert", "kind": "text", "expected": "User Management"}
    ]


@pytest.mark.parametrize(
    "text",
    [
        'Check the page shows "Done"',
        'The page should say "Done"',
        'Verify "Done" is displayed',
        'Confirm that the screen displays "Done"',
        'Check "Done"',
    ],
)
def test_the_many_ways_people_write_an_assertion(text):
    assert steps(text) == [{"action": "assert", "kind": "text", "expected": "Done"}]


def test_check_url():
    assert steps("Check the URL contains /users") == [
        {"action": "assert", "kind": "url_contains", "expected": "/users"}
    ]


def test_check_status():
    assert steps("Check the status is 403") == [
        {"action": "assert", "kind": "status", "expected": 403}
    ]


def test_check_something_is_absent():
    assert steps("Check I should not see the Delete button") == [
        {"action": "assert", "kind": "not_visible", "target": {"describe": "the Delete button"}}
    ]


def test_check_something_is_present():
    assert steps("Check I can see the Save button") == [
        {"action": "assert", "kind": "visible", "target": {"describe": "the Save button"}}
    ]


# --- api --------------------------------------------------------------------

def test_api_call_with_a_role_keeps_the_capability_not_an_alias():
    """Pinning an alias here would make the test non-portable; the broker picks
    at run time under least privilege."""
    assert steps("Call GET /api/users as an admin, expecting 200") == [
        {
            "action": "api_request", "method": "GET", "path": "/api/users",
            "capability": "manage_users", "expect_status": 200,
        }
    ]


def test_api_call_with_an_exact_alias_uses_it():
    assert steps("Call GET /api/users as ADMIN_USER")[0]["identity"] == "ADMIN_USER"


def test_api_call_written_as_a_bare_verb():
    assert steps("GET /api/users should return 403")[0]["expect_status"] == 403


def test_api_call_without_an_actor_is_unauthenticated():
    assert "identity" not in steps("Call GET /api/health")[0]


# --- waiting and evidence ---------------------------------------------------

def test_wait_for_something():
    assert steps('Wait for the page to show "Done"')[0] == {
        "action": "wait_for", "target": {"describe": "Done"}, "timeout_ms": 15000
    }


def test_a_fixed_wait_works_but_warns():
    built = compile_line("Wait 2 seconds", CTX)
    assert built.steps == [{"action": "pause", "seconds": 2}]
    assert "flaky" in built.warning


def test_a_very_long_wait_is_capped():
    assert steps("Wait 600 seconds")[0]["seconds"] == 30


def test_screenshot_names_are_turned_into_filenames():
    assert steps('Take a screenshot called "after checkout"')[0]["name"] == "after-checkout"


def test_a_note_produces_no_step():
    built = compile_line("Note: covers ticket QA-4417", CTX)
    assert built.steps == [] and "QA-4417" in built.explain


# --- prepositions inside labels (regression) --------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Click the Sign in button",
        "Press the Sign in button",
        "Click the Log in button",
        "Click the Opt in checkbox",
    ],
)
def test_a_preposition_inside_a_label_is_not_read_as_a_row(text):
    """"Click the Sign in button" must not parse as what="the Sign",
    within="button"."""
    target = steps(text)[0]["target"]
    assert "within" not in target
    assert target["describe"].lower().endswith(("button", "checkbox"))


def test_a_fully_quoted_label_is_never_split():
    assert steps('Click "Save for later"')[0]["target"] == {"describe": "Save for later"}


def test_row_scoping_needs_the_word_row_for_in():
    assert steps('Click "Edit" in the Kit Osei row')[0]["target"]["within"] == "Kit Osei"


def test_for_still_scopes_without_the_word_row():
    assert steps("Click Disable for Rae Rivera")[0]["target"]["within"] == "Rae Rivera"
