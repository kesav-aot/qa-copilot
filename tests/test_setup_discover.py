"""Finding the sign-in form removes the last step that needed a developer.

It has to work on pages that were not built with testing in mind, so the
fallback chain is exercised on three quite different forms.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_copilot.executor.browser import open_session
from qa_copilot.setup.discover import discover_login, find_error_target

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
async def browser(copilot, tmp_path):
    session = await open_session(copilot.config.environment("demo"), tmp_path)
    try:
        yield session
    finally:
        await session.close()


async def test_test_ids_are_preferred_when_present(browser, demo_server):
    found = await discover_login(browser.page, f"{demo_server}/login")
    assert found.complete
    assert found.username.target.testid == "login-username"
    assert found.password.target.testid == "login-password"
    assert found.submit.target.testid == "login-submit"


async def test_labels_are_used_when_there_are_no_test_ids(browser):
    found = await discover_login(browser.page, (FIXTURES / "labelled-login.html").as_uri())
    assert found.complete
    assert found.username.target.label == "Work email"
    assert found.password.target.label == "Password"
    assert found.submit.target.name == "Sign in to your account"


async def test_placeholders_and_names_are_the_next_fallback(browser):
    found = await discover_login(browser.page, (FIXTURES / "bare-login.html").as_uri())
    assert found.complete
    assert found.username.target.describe == "Username", "placeholder"
    assert found.password.target.css == '[name="j_password"]', "name attribute"
    assert found.submit.target.name == "Log On"


async def test_a_page_with_no_form_says_so_and_shows_what_is_there(browser):
    found = await discover_login(browser.page, (FIXTURES / "no-login.html").as_uri())
    assert not found.complete
    assert any("password field" in p for p in found.problems)
    assert found.inventory, "must show what the page does contain"
    assert any("Sign in" in entry for entry in found.inventory.get("link", []))


async def test_how_each_field_was_found_is_explained_in_words(browser, demo_server):
    found = await discover_login(browser.page, f"{demo_server}/login")
    assert found.username.describe() == 'test id "login-username"'
    assert "button labelled" in found.submit.describe() or "test id" in found.submit.describe()


async def test_the_error_element_is_found_after_a_bad_sign_in(browser, demo_server):
    """Knowing this lets a wrong password fail immediately instead of timing out."""
    page = browser.page
    await page.goto(f"{demo_server}/login")
    await page.get_by_test_id("login-username").fill("nobody@example.invalid")
    await page.get_by_test_id("login-password").fill("wrong-password-here")
    await page.get_by_test_id("login-submit").click()
    target = await find_error_target(page)
    assert target is not None
    assert target.testid == "login-error"


async def test_no_error_element_on_a_clean_page(browser, demo_server):
    await browser.page.goto(f"{demo_server}/login")
    assert await find_error_target(browser.page) is None


async def test_a_third_credential_field_is_found(browser):
    """Some sign-in forms want a PIN as well. Nothing can declare that ahead of
    time — the form has to be looked at, which is the point of this module."""
    found = await discover_login(browser.page, (FIXTURES / "pin-login.html").as_uri())
    assert found.complete
    assert list(found.extras) == ["pin"], found.extras
    assert found.extras["pin"].target.css == "#n" or found.extras["pin"].target.label


async def test_an_ordinary_form_reports_no_extra_fields(browser, demo_server):
    """The common case must not sprout fields that are not there."""
    found = await discover_login(browser.page, f"{demo_server}/login")
    assert found.extras == {}


async def test_hidden_fields_are_not_asked_for(browser):
    """A CSRF token is not a credential a person can be asked to type."""
    found = await discover_login(browser.page, (FIXTURES / "pin-login.html").as_uri())
    assert "csrf" not in found.extras
