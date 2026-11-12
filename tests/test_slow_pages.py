"""Real applications are slower than the demo.

The failure this file exists for, reported from a real product:

    Setup could not finish: Locator.fill: Timeout 30000ms exceeded.
      - waiting for get_by_label("Username or email").first

The form was rendered by JavaScript. Discovery looked for it the instant the
load event fired, and the fill was issued with no wait and no explicit timeout,
so it sat on Playwright's fixed default and then reported a field that had
simply not appeared yet as though the details were wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qa_copilot.executor.browser import open_session
from qa_copilot.setup.discover import discover_login
from qa_copilot.setup.wizard import run_wizard

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "Adm1n-Demo-Pass!"
USERNAME = "admin@qa.local"


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


async def _wizard(workspace, demo_server, *, timeout_ms: int) -> int:
    answers = iter(["y", "admin", "browse", USERNAME])
    return await run_wizard(
        config_dir=workspace / "config",
        url=demo_server,
        environment="slow",
        login_path="/slow-login",
        headless=True,
        secrets_file=workspace / ".env",
        work_dir=workspace,
        reader=lambda _p="": next(answers),
        password_reader=lambda _p="": PASSWORD,
        setup_timeout_ms=timeout_ms,
    )


async def test_a_form_rendered_late_is_still_found(copilot):
    """Looking the instant the page loads reports a page with no form at all."""
    session = await open_session(copilot.config.environment("demo"), Path("artifacts"))
    try:
        found = await discover_login(
            session.page, (FIXTURES / "slow-login.html").as_uri(), timeout_ms=30_000
        )
        assert found.complete, found.problems
        assert found.username.target.label == "Username or email"
    finally:
        await session.close()


async def test_setup_completes_against_a_form_rendered_late(workspace, demo_server):
    assert await _wizard(workspace, demo_server, timeout_ms=60_000) == 0

    environments = yaml.safe_load((workspace / "config" / "environments.yaml").read_text())
    assert environments["environments"]["slow"]["base_url"] == demo_server
    assert "QA_SECRET__SLOW__ADMIN__PASSWORD" in (workspace / ".env").read_text()


async def test_the_timeout_is_actually_honoured(workspace, demo_server):
    """With too little patience it must fail — otherwise the setting is fiction
    and the fix above proves nothing."""
    assert await _wizard(workspace, demo_server, timeout_ms=1_500) != 0
    assert not (workspace / ".env").exists(), "a refusal must write nothing"


async def test_a_test_run_signs_in_to_a_slow_form(copilot, demo_server, monkeypatch):
    """Not just setup: the executor waits for the fields too."""
    from qa_copilot.config import LoginRecipe
    from qa_copilot.dsl.schema import Target

    env = copilot.config.environment("demo")
    env.login = LoginRecipe(
        path="/slow-login",
        username_target=Target(label="Username or email"),
        password_target=Target(label="Password"),
        submit_target=Target(describe="Sign in"),
        success_url_contains="/dashboard",
    )
    identity = copilot.broker.resolve(
        identity="ADMIN_USER", capability=None, environment="demo"
    )
    creds = copilot.broker.credentials(identity)
    session = await open_session(env, Path("artifacts"), timeout_ms=30_000)
    try:
        result = await session.login(env.login, creds)
        assert result["authenticated"], result
    finally:
        await session.close()


def test_the_timeouts_are_configurable_in_settings():
    from qa_copilot.config import Config

    assert Config().timeout_ms == 30_000
    assert Config().setup_timeout_ms == 60_000
    raised = Config(timeout_ms=90_000, setup_timeout_ms=120_000)
    assert raised.timeout_ms == 90_000


def test_the_settings_file_actually_changes_the_timeout(tmp_path):
    """A setting the loader never reads is fiction.

    load_config builds Config with named arguments, so a field nobody wired up
    keeps its default however the file is written — which is exactly what the
    first version of these two did.
    """
    from qa_copilot.config import load_config

    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(
        "timeout_ms: 45000\nsetup_timeout_ms: 90000\n"
        "secrets:\n  provider: env\npolicy:\n  allowed_environments: [demo]\n"
    )
    loaded = load_config(config)
    assert loaded.timeout_ms == 45_000
    assert loaded.setup_timeout_ms == 90_000


def test_the_packaged_defaults_document_both_knobs():
    """Someone with a slow application has to be able to find them."""
    text = (Path(__file__).resolve().parents[1] / "packaging" / "config-defaults"
            / "settings.yaml").read_text()
    assert "timeout_ms:" in text
    assert "setup_timeout_ms:" in text


async def _wizard_output(workspace, demo_server, login_path: str, timeout_ms: int):
    import contextlib
    from io import StringIO

    answers = iter(["y", "admin", "browse", USERNAME])
    narration = StringIO()
    with contextlib.redirect_stdout(narration):
        code = await run_wizard(
            config_dir=workspace / "config",
            url=demo_server,
            environment="target",
            login_path=login_path,
            headless=True,
            secrets_file=workspace / ".env",
            work_dir=workspace,
            reader=lambda _p="": next(answers),
            password_reader=lambda _p="": PASSWORD,
            setup_timeout_ms=timeout_ms,
        )
    return code, narration.getvalue()


async def test_a_timeout_is_explained_not_dumped(workspace, demo_server):
    """The reported failure was a raw Playwright message naming a field, which
    reads as though the credentials were wrong rather than the field missing."""
    code, said = await _wizard_output(workspace, demo_server, "/never-visible-login", 2_000)
    assert code != 0
    assert "did not become usable" in said, said[-500:]
    assert "Locator" not in said, "a raw Playwright message is not an explanation"
    assert "what is on that page now" in said.lower(), (
        "and it must show what IS there, which is how a dead end becomes an edit"
    )


async def test_the_form_is_not_reloaded_before_it_is_filled(workspace, demo_server):
    """The reported failure. An OIDC authorisation endpoint needs fresh
    parameters, so asking for the bare path a second time returns an error page.
    Setup found the form, reloaded the same address, and then waited sixty
    seconds for fields that were no longer on the page.
    """
    code, said = await _wizard_output(workspace, demo_server, "/once-login", 20_000)
    assert code == 0, said[-700:]
    assert "QA_SECRET__TARGET__ADMIN__PASSWORD" in (workspace / ".env").read_text()


def _sso(demo_server: str, username: str, password: str, workspace):
    """The app under one host name, its sign-in site under another - which is
    what formsflow and Keycloak look like to a browser."""
    from qa_copilot.setup import webui

    port = demo_server.rsplit(":", 1)[-1]
    app_url = f"http://localhost:{port}"
    sign_in = f"{demo_server}/idp/auth?redirect_uri={app_url}/listing"
    session = webui.SetupSession(workspace / "config", workspace / ".env", workspace)
    result = session.provision(
        {
            "app_url": app_url,
            "login_path": sign_in,
            "environment": "sso",
            "account": "designer",
            "capabilities": "browse, Design Forms",
            "username": username,
            "password": password,
        }
    )
    return result, app_url, sign_in


# Synchronous on purpose: provision() calls asyncio.run() itself, which cannot
# be nested inside a running loop.
def test_setup_accepts_an_absolute_sign_in_address(workspace, demo_server):
    import yaml

    result, app_url, sign_in = _sso(demo_server, USERNAME, PASSWORD, workspace)
    assert result["state"] == "done", result["detail"][-600:]

    environments = yaml.safe_load((workspace / "config" / "environments.yaml").read_text())
    entry = environments["environments"]["sso"]
    assert entry["base_url"] == app_url, "the app keeps its own address"
    assert entry["login"]["path"] == sign_in, "and the form keeps the one it was given"
    # Signed in means back in the app - so the page saved as proof of it is
    # the app's, never a page on the sign-in site.
    assert entry["login"]["success_url_contains"] == "/listing"

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    assert identities["identities"]["DESIGNER_USER"]["capabilities"] == ["browse", "design_forms"]


def test_staying_on_the_sign_in_site_is_not_signing_in(workspace, demo_server):
    """The formsflow report: setup said "Signed in" and ended on the Keycloak
    URL. An account with a temporary password is shown "Update password" there,
    with no error message, and never reaches the app."""
    result, _, _ = _sso(demo_server, "new@qa.local", "Temp-Demo-Pass!", workspace)
    assert result["state"] == "failed", result
    assert "never came back" in result["detail"]
    assert "Update password" in result["detail"]
    assert not (workspace / "config" / "environments.yaml").exists()
    assert not (workspace / ".env").exists()


def test_a_wrong_password_on_the_sign_in_site_is_reported(workspace, demo_server):
    result, _, _ = _sso(demo_server, USERNAME, "definitely-not-right", workspace)
    assert result["state"] == "failed", result
    assert "Invalid username or password" in result["detail"]
    assert not (workspace / "config" / "environments.yaml").exists()
