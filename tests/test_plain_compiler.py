"""File structure, headers, and — most importantly — the errors.

For a non-coding user the error path *is* the product, so it is tested harder
than the happy path.
"""

from __future__ import annotations

from qa_copilot.plain import Context, compile_text, looks_like_plain_english

CTX = Context(
    identities=["ADMIN_USER", "STANDARD_USER"],
    capabilities=["browse", "manage_users"],
    environments=["demo", "staging"],
    default_environment="demo",
)

GOOD = """# Admin sees the user list
Environment: demo
Tags: smoke, authz
Description: The daily check.

Log in as an admin
Go to /users
Check the page shows "User Management"
"""


# --- structure --------------------------------------------------------------

def test_a_complete_file_compiles_with_its_headers():
    result = compile_text(GOOD, CTX)
    assert result.ok
    plan = result.tests[0].plan
    assert plan["name"] == "Admin sees the user list"
    assert plan["environment"] == "demo"
    assert plan["tags"] == ["smoke", "authz"]
    assert plan["description"] == "The daily check."
    assert len(plan["steps"]) == 3


def test_each_line_is_reported_back_in_plain_words():
    readings = compile_text(GOOD, CTX).tests[0].readings
    assert [r.line for r in readings] == [6, 7, 8]
    assert "log in as" in readings[0].explain
    assert "go to /users" in readings[1].explain


def test_a_heading_starts_a_new_test():
    text = "# One\nGo to /a\nCheck the page shows \"A\"\n\n# Two\nGo to /b\nCheck the page shows \"B\"\n"
    result = compile_text(text, CTX)
    assert [t.name for t in result.tests] == ["One", "Two"]
    assert result.ok


def test_a_test_colon_line_also_starts_one():
    result = compile_text('Test: Named this way\nGo to /a\nCheck the page shows "A"\n', CTX)
    assert result.tests[0].name == "Named this way"


def test_a_test_id_prefix_is_stripped_from_the_name():
    result = compile_text('# TC-1001: Admin sees users\nGo to /a\nCheck "A"\n', CTX)
    assert result.tests[0].name == "Admin sees users"


def test_numbered_and_bulleted_steps_are_accepted():
    result = compile_text("# T\n1. Go to /a\n2) Click Save\n- Check \"A\"\n", CTX)
    assert result.ok
    assert [s["action"] for s in result.tests[0].plan["steps"]] == ["navigate", "click", "assert"]


def test_comment_lines_are_ignored():
    result = compile_text('# T\n// a comment\nGo to /a\n-- another\nCheck "A"\n', CTX)
    assert len(result.tests[0].plan["steps"]) == 2


def test_a_colon_inside_a_step_is_not_read_as_a_header():
    result = compile_text('# T\nGo to /a\nCheck the page shows "Total: 42"\n', CTX)
    assert result.ok
    assert result.tests[0].plan["steps"][1]["expected"] == "Total: 42"


def test_the_environment_header_is_honoured():
    result = compile_text("# T\nEnvironment: staging\nGo to /a\nCheck \"A\"\n", CTX)
    assert result.tests[0].plan["environment"] == "staging"


def test_an_unknown_environment_lists_the_real_ones():
    result = compile_text("# T\nEnvironment: mars\nGo to /a\nCheck \"A\"\n", CTX)
    problem = result.tests[0].problems[0]
    assert "no environment called" in problem.message
    assert "demo" in problem.suggestion


# --- errors that teach ------------------------------------------------------

def test_an_unknown_line_is_an_error_never_a_silent_skip():
    result = compile_text("# T\nGo to /a\nFrobnicate the widget\nCheck \"A\"\n", CTX)
    assert not result.ok
    assert result.tests[0].plan is None
    problem = result.problems[0]
    assert problem.line == 3
    assert "do not understand" in problem.message


def test_a_typo_gets_the_right_suggestion():
    result = compile_text("# T\nClik the Save button\n", CTX)
    assert result.problems[0].suggestion == "Click the Save button"


def test_an_unrelated_line_gets_an_honest_fallback_not_a_misleading_guess():
    result = compile_text("# T\nFrobnicate the widget\n", CTX)
    assert "qa-copilot words" in result.problems[0].suggestion


def test_a_password_in_the_file_is_caught_before_anything_else():
    result = compile_text("# T\nLog in with password Hunter2Example\n", CTX)
    problem = result.problems[0]
    assert "real password" in problem.message
    assert "Log in as an admin" in problem.suggestion


def test_the_password_is_masked_when_the_line_is_echoed_back():
    result = compile_text("# T\nLog in with password Hunter2Example\n", CTX)
    assert "Hunter2Example" not in result.problems[0].text
    assert "********" in result.problems[0].text


def test_an_api_token_in_the_file_is_caught_too():
    result = compile_text(
        "# T\nCall GET /api/x with token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.sig\n", CTX
    )
    assert "real password or key" in result.problems[0].message


def test_a_file_with_no_steps_says_so():
    assert "no steps" in compile_text("# Just a heading\n", CTX).problems[0].message


def test_an_empty_file_says_so():
    assert "empty" in compile_text("\n\n", CTX).problems[0].message


# --- warnings ---------------------------------------------------------------

def test_a_test_that_checks_nothing_is_warned_about():
    result = compile_text("# T\nGo to /a\nClick Save\n", CTX)
    assert result.ok, "it should still run"
    assert any("never checks anything" in w for w in result.tests[0].warnings)


def test_a_guessed_page_address_is_warned_about():
    result = compile_text('# T\nGo to the Orders page\nCheck "x"\n', CTX)
    assert any("worked out" in w for w in result.tests[0].warnings)


# --- format detection -------------------------------------------------------

def test_plain_english_is_told_apart_from_a_dsl_plan():
    assert looks_like_plain_english(GOOD)
    assert not looks_like_plain_english(
        "version: 1\nname: x\nenvironment: demo\nsteps:\n  - action: navigate\n    path: /a\n"
    )
    assert not looks_like_plain_english('{"version": 1}')


# --- the machine-readable shape ---------------------------------------------

def test_the_dict_form_pairs_what_you_wrote_with_what_it_understood():
    payload = compile_text(GOOD, CTX).to_dict()
    assert payload["understood"] is True
    step = payload["tests"][0]["steps"][0]
    assert step["you_wrote"] == "Log in as an admin"
    assert "log in as" in step["i_understood"]
