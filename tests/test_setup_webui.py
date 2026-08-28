"""The browser setup page — the route for a QA engineer with no terminal.

The point of this file is the last two tests: the credential must reach `.env`
and nothing else, and no endpoint may hand it back. Everything the assistant is
allowed to learn goes through `public_status`, which builds its reply from fixed
keys rather than echoing what the form sent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest
import yaml

from qa_copilot.setup import webui

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


@pytest.fixture
def session(workspace):
    s = webui.start(workspace / "config", workspace / ".env", workspace)
    yield s
    s.httpd.shutdown()


def get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def submit(session, **fields) -> dict:
    form = {
        "t": session.token,
        "environment": "myapp",
        "login_path": "/login",
        "account": "admin",
        "capabilities": "browse,manage_users",
        "username": USERNAME,
        "password": PASSWORD,
        **fields,
    }
    data = urllib.parse.urlencode(form).encode()
    with urllib.request.urlopen(f"{session.url.split('?')[0]}submit", data, timeout=120) as r:
        return json.loads(r.read())


# --- the boundary -----------------------------------------------------------


def test_it_listens_on_loopback_only(session):
    assert session.httpd.server_address[0] == "127.0.0.1"


def test_the_page_needs_the_token(session):
    base = session.url.split("?")[0]
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base)
    assert exc.value.code == 403


def test_a_wrong_token_is_refused(session):
    base = session.url.split("?")[0]
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "?t=not-the-token")
    assert exc.value.code == 403


def test_the_form_is_served_with_the_token(session):
    status, body = get(session.url)
    assert status == 200
    assert "Set up QA Copilot" in body
    assert 'type="password"' in body


def test_submitting_without_the_token_is_refused(session):
    data = urllib.parse.urlencode({"t": "wrong", "app_url": "http://x"}).encode()
    req = urllib.request.Request(f"{session.url.split('?')[0]}submit", data)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 403


# --- the work it does -------------------------------------------------------


def test_it_writes_a_working_setup(session, workspace, demo_server):
    result = submit(session, app_url=demo_server)
    assert result["state"] == "done", result
    assert result["alias"] == "ADMIN_USER"
    assert result["environment"] == "myapp"

    environments = yaml.safe_load((workspace / "config" / "environments.yaml").read_text())
    entry = environments["environments"]["myapp"]
    assert entry["base_url"] == demo_server
    assert entry["login"]["username_target"] == {"testid": "login-username"}

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    assert identities["identities"]["ADMIN_USER"]["capabilities"] == ["browse", "manage_users"]


def test_a_wrong_password_is_reported_and_writes_nothing(session, workspace, demo_server):
    result = submit(session, app_url=demo_server, password="definitely-not-right")
    assert result["state"] == "failed"
    assert not (workspace / ".env").exists()


def test_missing_details_are_refused_before_a_browser_opens(session, workspace):
    result = submit(session, app_url="", username="", password="")
    assert result["state"] == "failed"
    assert not (workspace / ".env").exists()


# --- the regressions that matter --------------------------------------------


def test_the_credential_only_ever_reaches_the_dotenv_file(session, workspace, demo_server):
    submit(session, app_url=demo_server)
    holding = [
        p for p in workspace.rglob("*") if p.is_file() and PASSWORD in p.read_text(errors="ignore")
    ]
    assert [p.name for p in holding] == [".env"]


def test_no_endpoint_hands_the_credential_back(session, demo_server):
    result = submit(session, app_url=demo_server)
    assert PASSWORD not in json.dumps(result)
    assert USERNAME not in json.dumps(result)

    _, status_body = get(f"{session.url.split('?')[0]}status?t={session.token}")
    assert PASSWORD not in status_body
    assert USERNAME not in status_body


def test_the_status_is_built_from_fixed_keys_not_echoed_input(session):
    """A future field on the form must not become a field on the status."""
    session.set_status(state="done", password="leaked", note="also leaked")
    assert set(session.public_status()) == {"state", "detail", "environment", "alias", "url"}


# --- the page itself, driven as a person would ------------------------------


async def test_the_form_works_in_a_real_browser(session, workspace, demo_server):
    """The endpoint tests bypass the page's own JavaScript; this does not."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(session.url)
            await page.fill("[name=app_url]", demo_server)
            await page.fill("[name=environment]", "myapp")
            await page.check("input[value=manage_users]")
            await page.fill("[name=username]", USERNAME)
            await page.fill("[name=password]", PASSWORD)
            await page.click("#go")
            await page.wait_for_function(
                "document.querySelector('#go').textContent === 'Saved'", timeout=90_000
            )
            body = await page.inner_text("#out")
            assert "ADMIN_USER" in body
            assert PASSWORD not in body
            assert await page.input_value("[name=password]") == "", (
                "the password must not be left sitting in the form afterwards"
            )
        finally:
            await browser.close()

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    assert "ADMIN_USER" in identities["identities"]
