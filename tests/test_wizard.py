"""`qa-copilot init`, driven end to end against the demo app.

The leak regression is the point of this file: an early version of the wizard
had two variables both called ``env_file``, and wrote the password into
``config/environments.yaml`` — a file that gets committed.
"""

from __future__ import annotations

import pytest
import yaml

from qa_copilot.setup.wizard import run_wizard

PASSWORD = "Adm1n-Demo-Pass!"
USERNAME = "admin@qa.local"


def scripted(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(
        "secrets:\n  provider: env\n  dotenv: .env\n"
        "policy:\n  allowed_environments: [demo]\n"
        "  blocked_environments: [production]\n"
        "  approval_required_risk: medium\n  max_steps: 100\n"
    )
    return tmp_path


async def run(workspace, demo_server, *answers, password=PASSWORD):
    return await run_wizard(
        config_dir=workspace / "config",
        secrets_file=workspace / ".env",
        work_dir=workspace,
        url=demo_server,
        reader=scripted(*answers),
        password_reader=lambda prompt: password,
    )


HAPPY = ("myapp", "/login", "y", "admin", "browse, manage_users", USERNAME)


async def test_the_wizard_writes_a_working_setup(workspace, demo_server):
    assert await run(workspace, demo_server, *HAPPY) == 0

    environments = yaml.safe_load((workspace / "config" / "environments.yaml").read_text())
    entry = environments["environments"]["myapp"]
    assert entry["base_url"] == demo_server
    assert entry["login"]["username_target"] == {"testid": "login-username"}
    assert entry["login"]["success_url_contains"] == "/dashboard"
    assert entry["login"]["failure_target"] == {"testid": "login-error"}, (
        "knowing this makes a wrong password fail fast instead of timing out"
    )

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    account = identities["identities"]["ADMIN_USER"]
    assert account["capabilities"] == ["browse", "manage_users"]
    assert account["password_ref"] == "secret://myapp/admin/password"


async def test_the_credential_only_ever_reaches_the_dotenv_file(workspace, demo_server):
    """The regression test for the leak."""
    await run(workspace, demo_server, *HAPPY)

    for path in workspace.rglob("*"):
        if not path.is_file() or path.name == ".env":
            continue
        assert PASSWORD not in path.read_text(errors="ignore"), f"password leaked into {path}"

    env = (workspace / ".env").read_text()
    assert "QA_SECRET__MYAPP__ADMIN__PASSWORD=" in env
    assert PASSWORD in env


async def test_no_secret_reference_is_written_into_a_config_file(workspace, demo_server):
    await run(workspace, demo_server, *HAPPY)
    environments = (workspace / "config" / "environments.yaml").read_text()
    assert "secret://" not in environments, "only identities reference secrets"
    assert USERNAME not in environments


async def test_the_generated_test_actually_runs(workspace, demo_server):
    from qa_copilot.config import load_config
    from qa_copilot.engine import QACopilot

    await run(workspace, demo_server, *HAPPY)

    import os

    os.environ["QA_SECRET__MYAPP__ADMIN__USERNAME"] = USERNAME
    os.environ["QA_SECRET__MYAPP__ADMIN__PASSWORD"] = PASSWORD
    try:
        config = load_config(workspace / "config")
        config.artifact_dir = str(workspace / "artifacts")
        copilot = QACopilot(
            config, state_dir=workspace / "state", config_dir=workspace / "config"
        )
        starter = (workspace / "my-first-test.txt").read_text()
        assert "Log in as ADMIN_USER" in starter
        result = await copilot.run_plain(starter)
        assert result["overall"] == "passed", result
    finally:
        os.environ.pop("QA_SECRET__MYAPP__ADMIN__USERNAME", None)
        os.environ.pop("QA_SECRET__MYAPP__ADMIN__PASSWORD", None)


async def test_the_environment_is_added_to_the_policy_allow_list(workspace, demo_server):
    await run(workspace, demo_server, *HAPPY)
    settings = yaml.safe_load((workspace / "config" / "settings.yaml").read_text())
    assert settings["policy"]["allowed_environments"] == ["demo", "myapp"]


async def test_a_wrong_password_stops_before_writing_anything(workspace, demo_server):
    code = await run(workspace, demo_server, *HAPPY, password="definitely-not-right")
    assert code == 1
    assert not (workspace / "config" / "environments.yaml").exists()
    assert not (workspace / ".env").exists()


async def test_declining_the_discovered_form_writes_nothing(workspace, demo_server):
    code = await run(workspace, demo_server, "myapp", "/login", "n")
    assert code == 1
    assert not (workspace / "config" / "environments.yaml").exists()


async def test_an_environment_that_already_exists_is_refused(workspace, demo_server):
    (workspace / "config" / "environments.yaml").write_text(
        "environments:\n  myapp:\n    base_url: http://elsewhere\n"
    )
    assert await run(workspace, demo_server, *HAPPY) == 2
    assert "elsewhere" in (workspace / "config" / "environments.yaml").read_text()


async def test_an_empty_password_is_refused(workspace, demo_server):
    assert await run(workspace, demo_server, *HAPPY, password="") == 2
    assert not (workspace / ".env").exists()


async def test_an_account_alias_that_already_exists_is_refused_before_any_write(
    workspace, demo_server
):
    """The second leak-shaped bug: a clash found too late leaves a half-setup.

    The alias was only checked when the identity was written — after the
    password and the environment had already gone to disk. The workspace was
    then left with a secret and an environment for an account that did not
    exist.
    """
    (workspace / "config" / "identities.yaml").write_text(
        "identities:\n  ADMIN_USER:\n    description: someone else\n"
        "    capabilities: [browse]\n"
    )

    assert await run(workspace, demo_server, *HAPPY) == 2

    assert not (workspace / ".env").exists(), "the password must not survive a refusal"
    assert not (workspace / "config" / "environments.yaml").exists()
    assert "someone else" in (workspace / "config" / "identities.yaml").read_text()
