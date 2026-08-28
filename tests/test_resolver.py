"""Finding an element from a phrase, against a real page.

The two behaviours that matter are refusals: never silently pick between
candidates, and never say "not found" without saying what *is* there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_copilot.dsl.schema import Target
from qa_copilot.executor.browser import open_session
from qa_copilot.executor.resolver import (
    ElementAmbiguous,
    ElementNotFound,
    page_inventory,
    parse_phrase,
    resolve,
)


# --- phrase parsing (no browser needed) -------------------------------------

@pytest.mark.parametrize(
    ("text", "name", "role", "index"),
    [
        ("the Save button", "Save", "button", None),
        ("Save", "Save", None, None),
        ("the second Disable button", "Disable", "button", 2),
        ("first Delete link", "Delete", "link", 1),
        ("the last row", "", "row", -1),
        ("Email field", "Email", "textbox", None),
        ("the Search box", "Search", "textbox", None),
        ('"Add to cart"', "Add to cart", None, None),
        ("the Plan dropdown", "Plan", "combobox", None),
        ("the Remember me checkbox", "Remember me", "checkbox", None),
        ("Settings menu item", "Settings", "menuitem", None),
    ],
)
def test_phrases_are_read_the_way_a_person_means_them(text, name, role, index):
    phrase = parse_phrase(text)
    assert (phrase.name, phrase.role, phrase.index) == (name, role, index)


def test_an_explicit_index_overrides_an_ordinal_word():
    assert parse_phrase("the second Disable button", index=3).index == 3


# --- against the live demo app ----------------------------------------------

@pytest.fixture
async def users_page(copilot, tmp_path):
    """Signed in as an admin, sitting on the Users page."""
    env = copilot.config.environment("demo")
    session = await open_session(env, Path(copilot.config.artifact_dir))
    try:
        creds = copilot.broker.credentials(copilot.config.identity("ADMIN_USER"))
        await session.login(env.login, creds)
        await session.navigate("/users")
        yield session.page
    finally:
        await session.close()


async def test_a_unique_phrase_resolves(users_page):
    locator = await resolve(users_page, Target(describe="the User Management heading"))
    assert await locator.inner_text() == "User Management"


async def test_naming_the_wrong_kind_of_thing_is_explained_not_silently_matched(users_page):
    """There is a link called "Users" but no heading called "Users". Matching the
    link would be the silent-wrong-pick this module exists to prevent."""
    with pytest.raises(ElementNotFound) as excinfo:
        await resolve(users_page, Target(describe="the Users heading"))
    message = str(excinfo.value)
    assert "you asked for a heading" in message
    assert "drop the word" in message


async def test_a_test_id_slug_is_tried_first(users_page):
    locator = await resolve(users_page, Target(describe="users-heading"))
    assert await locator.count() == 1


async def test_a_phrase_matching_several_things_refuses_and_lists_them(users_page):
    with pytest.raises(ElementAmbiguous) as excinfo:
        await resolve(users_page, Target(describe="Disable"))
    message = str(excinfo.value)
    assert "matches 2 things" in message
    assert "Rae Rivera" in message and "Kit Osei" in message
    assert "Narrow it down" in message


async def test_a_row_scope_makes_it_unambiguous(users_page):
    locator = await resolve(users_page, Target(describe="Disable", within="Rae Rivera"))
    assert await locator.get_attribute("data-testid") == "disable-user-1"


async def test_an_ordinal_makes_it_unambiguous(users_page):
    locator = await resolve(users_page, Target(describe="the second Disable button"))
    assert await locator.get_attribute("data-testid") == "disable-user-2"


async def test_an_index_out_of_range_says_how_many_there_are(users_page):
    with pytest.raises(ElementNotFound, match="only found 2"):
        await resolve(users_page, Target(describe="Disable", index=9))


async def test_a_missing_element_lists_what_is_on_the_page(users_page):
    with pytest.raises(ElementNotFound) as excinfo:
        await resolve(users_page, Target(describe="the Export button"))
    message = str(excinfo.value)
    assert "could not find" in message
    assert "Dashboard" in message, "should list the links that do exist"
    assert "users-heading" in message, "should offer the test ids it can see"


async def test_a_missing_row_is_reported_as_such(users_page):
    with pytest.raises(ElementNotFound, match="row or section containing"):
        await resolve(users_page, Target(describe="Disable", within="Nobody At All"))


async def test_the_inventory_groups_things_by_kind(users_page):
    inventory = await page_inventory(users_page)
    assert "link" in inventory and "button" in inventory
    assert any("Sign out" in entry for entry in inventory["link"])


async def test_precise_targets_still_work_unchanged(users_page):
    locator = await resolve(users_page, Target(testid="disable-user-1"))
    assert await locator.count() == 1
