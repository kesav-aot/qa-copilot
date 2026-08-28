"""Writing configuration must never destroy a file, and never leak a secret."""

from __future__ import annotations

import pytest
import yaml

from qa_copilot.setup.writer import (
    ConfigWriteError,
    allow_environment,
    append_secrets,
    append_under_key,
)


# --- the guard that matters most -------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["environments.yaml", "identities.yaml", "settings.yml", "plan.json", "notes.md", "a.toml"],
)
def test_secrets_are_refused_anywhere_but_a_dotenv_file(tmp_path, name):
    """A caller that mixes up two path variables must not be able to write a
    password into a file that gets committed. This is checked structurally, not
    left to the caller getting it right."""
    with pytest.raises(ConfigWriteError, match="refusing to write secrets"):
        append_secrets(tmp_path / name, {"QA_SECRET__X": "hunter2"})
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.qa"])
def test_secrets_are_allowed_in_a_dotenv_file(tmp_path, name):
    written = append_secrets(tmp_path / name, {"QA_SECRET__X": "hunter2"})
    assert written == ["QA_SECRET__X"], "the return value must not contain the value"
    assert "hunter2" in (tmp_path / name).read_text()


def test_a_dotenv_file_is_locked_down(tmp_path):
    path = tmp_path / ".env"
    append_secrets(path, {"QA_SECRET__X": "hunter2"})
    assert path.stat().st_mode & 0o077 == 0, "others must not be able to read it"


def test_overwriting_an_existing_secret_is_refused(tmp_path):
    path = tmp_path / ".env"
    append_secrets(path, {"QA_SECRET__X": "one"})
    with pytest.raises(ConfigWriteError, match="already defines"):
        append_secrets(path, {"QA_SECRET__X": "two"})
    assert "one" in path.read_text()


# --- config files -----------------------------------------------------------

def test_comments_survive_a_write(tmp_path):
    path = tmp_path / "environments.yaml"
    path.write_text("# how to reach each environment\nenvironments:\n\n  demo:\n    base_url: http://x\n")
    append_under_key(path, "environments", "qa", {"base_url": "https://qa"})
    text = path.read_text()
    assert "# how to reach each environment" in text
    assert set(yaml.safe_load(text)["environments"]) == {"demo", "qa"}


def test_writing_to_a_file_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "identities.yaml"
    append_under_key(path, "identities", "ADMIN_USER", {"capabilities": ["browse"]})
    assert yaml.safe_load(path.read_text())["identities"]["ADMIN_USER"]["capabilities"] == ["browse"]


def test_a_duplicate_entry_is_refused_rather_than_merged(tmp_path):
    path = tmp_path / "environments.yaml"
    append_under_key(path, "environments", "qa", {"base_url": "https://one"})
    with pytest.raises(ConfigWriteError, match="already has an entry"):
        append_under_key(path, "environments", "qa", {"base_url": "https://two"})
    assert "one" in path.read_text() and "two" not in path.read_text()


def test_a_nested_body_round_trips(tmp_path):
    path = tmp_path / "environments.yaml"
    body = {
        "base_url": "https://qa",
        "login": {
            "path": "/signin",
            "username_target": {"label": "Email"},
            "submit_target": {"role": "button", "name": "Sign in"},
        },
    }
    append_under_key(path, "environments", "qa", body)
    assert yaml.safe_load(path.read_text())["environments"]["qa"] == body


def test_an_awkward_key_is_quoted_rather_than_breaking_the_file(tmp_path):
    path = tmp_path / "environments.yaml"
    append_under_key(path, "environments", "odd: name\nwith newline", {"base_url": "x"})
    assert "odd: name\nwith newline" in yaml.safe_load(path.read_text())["environments"]


def test_a_write_that_would_corrupt_the_file_changes_nothing(tmp_path, monkeypatch):
    """The safety property: if the result would not parse, the original stands."""
    import qa_copilot.setup.writer as writer

    path = tmp_path / "environments.yaml"
    path.write_text("environments:\n  demo:\n    base_url: http://x\n")
    before = path.read_text()

    monkeypatch.setattr(writer.yaml, "safe_dump", lambda *a, **k: "qa: [unclosed\n")
    with pytest.raises(ConfigWriteError, match="invalid YAML"):
        append_under_key(path, "environments", "qa", {"base_url": "x"})
    assert path.read_text() == before


# --- the policy allow-list --------------------------------------------------

def test_a_new_environment_is_added_to_the_allow_list(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("policy:\n  allowed_environments: [demo]\n  max_steps: 100\n")
    assert allow_environment(path, "qa")
    data = yaml.safe_load(path.read_text())
    assert data["policy"]["allowed_environments"] == ["demo", "qa"]
    assert data["policy"]["max_steps"] == 100, "the rest of the policy must be untouched"


def test_an_environment_already_allowed_is_left_alone(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("policy:\n  allowed_environments: [demo, qa]\n")
    assert not allow_environment(path, "qa")


def test_an_empty_allow_list_means_everything_and_is_not_narrowed(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("policy:\n  allowed_environments: []\n")
    assert not allow_environment(path, "qa")
