"""Skills reach the assistant through the server, and a team can add its own.

A Claude Desktop extension is only an MCP server, so a skills folder shipped
beside it is never read. These tests are about the route that does work.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from qa_copilot import mcp_server
from qa_copilot.skills import SkillLibrary, builtin_dir

ROOT = Path(__file__).resolve().parents[1]

GOOD = """# Testing the claims portal

Sign in as someone who can manage users.

## Where things are
| Page | Address |
|---|---|
| Claims | /claims |
"""


def test_the_built_in_skills_are_found_and_described():
    skills = {s["name"]: s for s in SkillLibrary(Path("/nonexistent")).list()}
    for name in (
        "qa-write-test", "qa-run-test", "qa-report", "qa-formsflow",
        "qa-project-skill", "qa-analyze-failure",
    ):
        assert name in skills, name
        assert skills[name]["source"] == "built-in"
        assert len(skills[name]["description"]) > 40


def test_every_skill_folder_in_the_repo_is_a_loadable_skill():
    """A SKILL.md with broken frontmatter would silently vanish from the list."""
    on_disk = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    listed = {s["name"] for s in SkillLibrary(Path("/nonexistent"), ROOT / "skills").list()}
    assert on_disk == listed


def test_a_skill_is_returned_without_its_frontmatter(copilot):
    out = copilot.get_skill("qa-report")
    assert out["instructions"].startswith("# ")
    assert "description:" not in out["instructions"][:200]


def test_an_unknown_skill_says_what_exists(copilot):
    out = copilot.get_skill("../../config/identities")
    assert "there is no skill called" in out["error"]
    assert "qa-write-test" in out["error"]


def test_a_team_can_save_its_own_skill_and_get_it_back(copilot):
    saved = copilot.save_skill(
        "acme-claims-portal",
        "How the Acme claims portal works; use when testing claim submission.",
        GOOD,
    )
    assert saved["saved"], saved
    assert Path(saved["path"]) == copilot.config.skills_path / "acme-claims-portal" / "SKILL.md"

    listed = {s["name"]: s for s in copilot.list_skills()["skills"]}
    assert listed["acme-claims-portal"]["source"] == "project"
    assert "claim submission" in listed["acme-claims-portal"]["description"]
    assert "/claims" in copilot.get_skill("acme-claims-portal")["instructions"]


def test_a_project_skill_replaces_a_built_in_one_of_the_same_name(copilot):
    saved = copilot.save_skill(
        "qa-report",
        "How this team wants results reported; use after every test run.",
        "# Reporting here\n\nAlways mention the ticket number in the summary line.\n",
    )
    assert saved["overrides_built_in"] is True
    skill = copilot.get_skill("qa-report")
    assert skill["source"] == "project"
    assert "ticket number" in skill["instructions"]
    # and the shipped file was not touched
    assert "ticket number" not in (ROOT / "skills" / "qa-report" / "SKILL.md").read_text()


def test_a_skill_dropped_into_the_folder_by_hand_is_picked_up(copilot):
    folder = copilot.config.skills_path
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "orders.md").write_text("# Testing orders\n\nOrders live at /orders.\n")
    listed = {s["name"]: s for s in copilot.list_skills()["skills"]}
    assert listed["orders"]["description"] == "Testing orders"


@pytest.mark.parametrize(
    "text",
    [
        "Sign in with password: Hunter2-Really!",
        "The admin api_key=sk-live-0123456789abcdef",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop",
    ],
)
def test_a_skill_containing_a_credential_is_refused(copilot, text):
    out = copilot.save_skill(
        "leaky-skill",
        "A skill that should never be written to disk at all.",
        f"# Testing\n\nA perfectly ordinary first paragraph of instructions.\n\n{text}\n",
    )
    assert out["saved"] is False
    assert "credential" in out["error"]
    assert not (copilot.config.skills_path / "leaky-skill").exists()


def test_a_skill_may_talk_about_passwords_without_containing_one(copilot):
    out = copilot.save_skill(
        "safe-skill",
        "A skill that mentions credentials only in the abstract, as it should.",
        "# Testing\n\nNever write a password. The password is fetched from the secret "
        "store; sign in as ADMIN_USER.\n",
    )
    assert out["saved"], out


@pytest.mark.parametrize("name", ["../escape", "a", "Has Spaces", "/etc/passwd", "x" * 80])
def test_a_skill_name_cannot_steer_where_the_file_is_written(copilot, name):
    out = copilot.save_skill(
        name, "A description that is long enough to be accepted.", GOOD
    )
    assert out["saved"] is False
    root = copilot.config.skills_path
    assert not root.exists() or not any(root.rglob("SKILL.md"))


def test_the_skills_travel_through_the_server(copilot, monkeypatch):
    monkeypatch.setattr(mcp_server, "copilot", lambda: copilot)
    names = {s["name"] for s in json.loads(mcp_server.list_skills())["skills"]}
    assert "qa-formsflow" in names
    body = json.loads(mcp_server.get_skill("qa-formsflow"))["instructions"]
    assert "Drag" in body


def test_built_in_skills_are_offered_as_prompts():
    """So they appear in the host's own menu, not only to the model."""
    prompts = {p.name for p in asyncio.run(mcp_server.mcp.list_prompts())}
    assert {"qa-write-test", "qa-report", "qa-formsflow"} <= prompts


def test_the_server_tells_the_model_to_load_skills_and_hand_over_reports():
    text = mcp_server.mcp.instructions
    assert "list_skills" in text and "get_skill" in text
    assert "html_report" in text


def test_built_in_skills_are_found_inside_an_installed_package(tmp_path, monkeypatch):
    """A Desktop install has no repository beside the package."""
    import qa_copilot.skills as skills

    package = tmp_path / "site-packages" / "qa_copilot"
    (package / "builtin_skills" / "qa-thing").mkdir(parents=True)
    (package / "builtin_skills" / "qa-thing" / "SKILL.md").write_text(
        "---\nname: qa-thing\ndescription: A thing.\n---\n\nBody.\n"
    )
    monkeypatch.setattr(skills, "__file__", str(package / "skills.py"))
    assert builtin_dir() == package / "builtin_skills"


def test_a_workspace_pointed_at_the_shipped_skills_cannot_overwrite_them():
    library = SkillLibrary(ROOT / "skills", ROOT / "skills")
    assert {s["source"] for s in library.list()} == {"built-in"}
    with pytest.raises(Exception, match="folder of their own"):
        library.save("qa-report", "A description that is long enough to pass.", GOOD)
