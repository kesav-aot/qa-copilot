"""Plans arrive from a model, so the library must never let a plan name decide
where a file lands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qa_copilot.dsl.schema import TestPlan
from qa_copilot.library import LibraryError, PlanLibrary, slugify

PLAN = {
    "version": 1,
    "name": "Admin can reach user management",
    "environment": "demo",
    "steps": [
        {"action": "navigate", "path": "/users"},
        {"action": "assert", "kind": "text", "expected": "User Management"},
    ],
}


@pytest.fixture
def library(tmp_path):
    suites = tmp_path / "suites.yaml"
    suites.write_text("suites:\n  smoke: [admin-can-reach-user-management]\n")
    return PlanLibrary(tmp_path / "plans", suites_file=suites)


def plan(**overrides) -> TestPlan:
    return TestPlan.model_validate({**PLAN, **overrides})


# --- slugs -----------------------------------------------------------------

def test_slug_is_derived_from_the_name():
    assert slugify("Admin can reach user management") == "admin-can-reach-user-management"


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "..\\..\\windows\\system32", "/absolute/path", "a/../../b"]
)
def test_a_traversal_attempt_in_a_plan_name_cannot_escape(library, name):
    saved = library.save(plan(name=name))
    path = Path(saved["path"]).resolve()
    assert library.root.resolve() == path.parent
    assert ".." not in path.parts


def test_a_name_with_no_usable_characters_is_refused():
    with pytest.raises(LibraryError, match="empty slug"):
        slugify("///")


def test_very_long_names_are_truncated():
    assert len(slugify("x" * 500)) == 80


# --- round trip ------------------------------------------------------------

def test_save_then_load_round_trips(library):
    info = library.save(plan())
    assert info["slug"] == "admin-can-reach-user-management"
    assert not info["replaced"]
    assert library.load("admin-can-reach-user-management").name == PLAN["name"]
    assert library.load(PLAN["name"]).name == PLAN["name"]


def test_saving_twice_reports_the_replacement(library):
    library.save(plan())
    assert library.save(plan())["replaced"] is True


def test_overwrite_can_be_refused(library):
    library.save(plan())
    with pytest.raises(LibraryError, match="already exists"):
        library.save(plan(), overwrite=False)


def test_saved_yaml_is_readable_by_a_human(library):
    body = yaml.safe_load(Path(library.save(plan())["path"]).read_text())
    assert body["name"] == PLAN["name"]
    assert body["steps"][0]["action"] == "navigate"


def test_loading_something_that_is_not_there_lists_what_is(library):
    library.save(plan())
    with pytest.raises(LibraryError, match="admin-can-reach-user-management"):
        library.load("nope")


def test_delete_removes_the_file(library):
    library.save(plan())
    assert library.delete("admin-can-reach-user-management")
    assert not library.delete("admin-can-reach-user-management")


# --- listing ---------------------------------------------------------------

def test_list_reports_the_fingerprint_and_shape(library):
    library.save(plan())
    entry = library.list()[0]
    assert entry["environment"] == "demo"
    assert entry["steps"] == 2
    assert len(entry["fingerprint"]) == 32


def test_a_corrupt_plan_file_is_listed_as_broken_not_crashed(library):
    library.save(plan())
    (library.root / "broken.yaml").write_text("steps: not-a-list\n")
    entries = {e["slug"]: e for e in library.list()}
    assert "error" in entries["broken"]
    assert "error" not in entries["admin-can-reach-user-management"]


def test_listing_an_absent_directory_is_empty_not_an_error(tmp_path):
    assert PlanLibrary(tmp_path / "nope").list() == []


# --- suites ----------------------------------------------------------------

def test_a_suite_resolves_when_every_member_exists(library):
    library.save(plan())
    assert library.resolve_suite("smoke") == ["admin-can-reach-user-management"]


def test_a_suite_missing_a_plan_says_which_one(library):
    with pytest.raises(LibraryError, match="admin-can-reach-user-management"):
        library.resolve_suite("smoke")


def test_an_unknown_suite_lists_the_defined_ones(library):
    with pytest.raises(LibraryError, match="smoke"):
        library.resolve_suite("nope")


def test_no_suites_file_means_no_suites(tmp_path):
    assert PlanLibrary(tmp_path / "plans").suites() == {}
