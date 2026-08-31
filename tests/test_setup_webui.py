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


@pytest.fixture(autouse=True)
def _no_leftover_secrets():
    """Filling an account sets os.environ so a running assistant sees it at once.

    That is deliberate, and it means one test's secrets would otherwise make the
    next one think the account was already configured.
    """
    import os

    def clear() -> None:
        for key in [k for k in os.environ if k.startswith("QA_SECRET__THEIRS__")]:
            del os.environ[key]

    clear()
    yield
    clear()


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
    assert 'id="look"' in body, "step one is to look at the sign-in page"


def test_no_credential_fields_exist_before_the_page_has_been_inspected(session):
    """They are built from what the form turns out to ask for, not assumed."""
    _, body = get(session.url)
    assert 'name="password"' not in body
    assert 'name="username"' not in body


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

            # Step one: the page asks the app what it wants, then builds itself.
            await page.click("#look")
            await page.wait_for_selector("[data-field=password]", timeout=90_000)
            fields = await page.eval_on_selector_all(
                "[data-field]", "els => els.map(e => e.dataset.field)"
            )
            assert fields == ["username", "password"], fields

            await page.fill("[data-field=username]", USERNAME)
            await page.fill("[data-field=password]", PASSWORD)
            await page.click("#go")
            await page.wait_for_function(
                "document.querySelector('#go').textContent === 'Saved'", timeout=90_000
            )
            body = await page.inner_text("#out")
            assert "ADMIN_USER" in body
            assert PASSWORD not in body
            assert await page.input_value("[data-field=password]") == "", (
                "the password must not be left sitting in the form afterwards"
            )
        finally:
            await browser.close()

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    assert "ADMIN_USER" in identities["identities"]


# --- completing an account that already exists ------------------------------
# The gap this closes: the page could only ever add a *new* environment and
# account. An identity already in identities.yaml with no secrets — written by
# hand, or by a colleague — could not be completed here at all.


def _existing_account(workspace, demo_server, *, extras: str = "") -> None:
    (workspace / "config" / "environments.yaml").write_text(
        "environments:\n"
        "  theirs:\n"
        f"    base_url: {demo_server}\n"
        "    login:\n"
        "      path: /login\n"
        "      username_target: { testid: login-username }\n"
        "      password_target: { testid: login-password }\n"
        "      submit_target: { testid: login-submit }\n"
        "      success_url_contains: /dashboard\n"
    )
    (workspace / "config" / "identities.yaml").write_text(
        "identities:\n"
        "  THEIR_USER:\n"
        "    description: An account someone added by hand.\n"
        "    capabilities: [browse]\n"
        "    username_ref: secret://theirs/them/username\n"
        "    password_ref: secret://theirs/them/password\n"
        f"{extras}"
        "    environments: [theirs]\n"
    )


def fill(session, alias="THEIR_USER", **fields) -> dict:
    form = {"t": session.token, "alias": alias, **fields}
    data = urllib.parse.urlencode(form).encode()
    with urllib.request.urlopen(f"{session.url.split('?')[0]}fill", data, timeout=120) as r:
        return json.loads(r.read())


def test_an_existing_account_is_listed_as_pending(workspace, demo_server):
    _existing_account(workspace, demo_server)
    pending = webui.pending_identities(workspace / "config")
    assert [p["alias"] for p in pending] == ["THEIR_USER"]
    assert pending[0]["fields"] == ["username", "password"]


def test_extra_login_fields_are_asked_for(workspace, demo_server):
    """A form wanting a PIN as well must ask for it, or the account stays unusable."""
    _existing_account(
        workspace, demo_server, extras="    extra_refs:\n      pin: secret://theirs/them/pin\n"
    )
    assert webui.pending_identities(workspace / "config")[0]["fields"] == [
        "username",
        "password",
        "pin",
    ]


def test_filling_an_existing_account_writes_its_secrets(session, workspace, demo_server):
    _existing_account(workspace, demo_server)
    result = fill(session, field_username=USERNAME, field_password=PASSWORD)
    assert result["state"] == "done", result
    assert result["alias"] == "THEIR_USER"

    written = (workspace / ".env").read_text()
    assert "QA_SECRET__THEIRS__THEM__USERNAME" in written
    assert "QA_SECRET__THEIRS__THEM__PASSWORD" in written


def test_a_wrong_password_for_an_existing_account_saves_nothing(session, workspace, demo_server):
    _existing_account(workspace, demo_server)
    result = fill(session, field_username=USERNAME, field_password="not-the-password")
    assert result["state"] == "failed"
    assert not (workspace / ".env").exists()


def test_a_missing_extra_field_is_refused(session, workspace, demo_server):
    _existing_account(
        workspace, demo_server, extras="    extra_refs:\n      pin: secret://theirs/them/pin\n"
    )
    result = fill(session, field_username=USERNAME, field_password=PASSWORD, field_pin="")
    assert result["state"] == "failed"
    assert "pin" in result["detail"]
    assert not (workspace / ".env").exists()


def test_extra_fields_are_written_with_their_own_reference(
    session, workspace, demo_server, monkeypatch
):
    """The demo app has no PIN field, so the sign-in check is stubbed; what is
    under test is that every reference, extras included, reaches the store."""
    _existing_account(
        workspace, demo_server, extras="    extra_refs:\n      pin: secret://theirs/them/pin\n"
    )

    async def signs_in(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(webui, "_verify_login", signs_in)
    result = fill(session, field_username=USERNAME, field_password=PASSWORD, field_pin="4821")
    assert result["state"] == "done", result
    written = (workspace / ".env").read_text()
    assert "QA_SECRET__THEIRS__THEM__PIN" in written


def test_the_filled_account_is_usable_without_a_restart(session, workspace, demo_server):
    """The provider reads os.environ at resolve time, so a running assistant
    must see the account become usable the moment the page is submitted."""
    _existing_account(workspace, demo_server)
    assert webui.pending_identities(workspace / "config")
    fill(session, field_username=USERNAME, field_password=PASSWORD)
    assert webui.pending_identities(workspace / "config") == []


# --- a form whose fields cannot be guessed ----------------------------------


def inspect(session, **fields) -> dict:
    form = {"t": session.token, "login_path": "/login", **fields}
    data = urllib.parse.urlencode(form).encode()
    with urllib.request.urlopen(f"{session.url.split('?')[0]}inspect", data, timeout=120) as r:
        return json.loads(r.read())


def test_inspection_reports_an_ordinary_forms_two_fields(session, demo_server):
    result = inspect(session, app_url=demo_server)
    assert result["ok"], result
    assert [f["name"] for f in result["fields"]] == ["username", "password"]


def test_inspection_finds_a_third_credential_and_names_it_as_the_page_does(
    session, demo_server
):
    """Nobody declared a PIN anywhere; the form was read and it asked for one."""
    result = inspect(session, app_url=demo_server, login_path="/pin-login")
    assert result["ok"], result
    assert [f["name"] for f in result["fields"]] == ["username", "password", "pin"]
    assert result["fields"][2]["label"] == "2nd Level Passcode"
    assert result["fields"][2]["kind"] == "password", "an unknown extra field is a secret"


def test_a_page_with_no_sign_in_form_is_reported_not_guessed(session, demo_server):
    result = inspect(session, app_url=demo_server, login_path="/no-such-page")
    assert not result["ok"]
    assert result["problems"]


def test_a_page_that_redirects_to_the_sign_in_form_still_works(session, demo_server):
    """Giving the address of a protected page is the obvious mistake to make;
    following the redirect means it is not a mistake at all."""
    result = inspect(session, app_url=demo_server, login_path="/dashboard")
    assert result["ok"], result
    assert [f["name"] for f in result["fields"]] == ["username", "password"]


def test_a_three_field_form_is_set_up_end_to_end(session, workspace, demo_server):
    """Discovery, sign-in check, config and secrets — all three credentials."""
    result = submit(
        session,
        app_url=demo_server,
        login_path="/pin-login",
        environment="pinapp",
        extra_pin="4821",
    )
    assert result["state"] == "done", result

    environments = yaml.safe_load((workspace / "config" / "environments.yaml").read_text())
    recipe = environments["environments"]["pinapp"]["login"]
    assert recipe["extra_targets"]["pin"], "the PIN's location must be recorded"

    identities = yaml.safe_load((workspace / "config" / "identities.yaml").read_text())
    assert identities["identities"]["ADMIN_USER"]["extra_refs"] == {
        "pin": "secret://pinapp/admin/pin"
    }
    assert "QA_SECRET__PINAPP__ADMIN__PIN" in (workspace / ".env").read_text()


def test_a_wrong_pin_is_rejected_like_a_wrong_password(session, workspace, demo_server):
    result = submit(
        session,
        app_url=demo_server,
        login_path="/pin-login",
        environment="pinapp",
        extra_pin="0000",
    )
    assert result["state"] == "failed"
    assert not (workspace / ".env").exists()


# --- what a person who just installed this is asked about -------------------


def test_the_page_reports_a_browser_problem_instead_of_dropping_the_connection(
    session, workspace, demo_server, monkeypatch
):
    """A fresh install has no browser yet. The handler used to let that escape,
    http.server closed the connection unanswered, and the page could only say
    'TypeError: Failed to fetch' — which points at the network, not the cause."""
    _existing_account(workspace, demo_server)

    async def no_browser(*_a, **_k):
        raise RuntimeError("Executable doesn't exist at .../ms-playwright/chromium-1234/chrome")

    monkeypatch.setattr("qa_copilot.executor.browser.open_session", no_browser)
    monkeypatch.setattr(webui, "_start_browser_download", lambda: None)

    result = fill(session, field_username=USERNAME, field_password=PASSWORD)
    assert result["state"] == "failed"
    assert "browser" in result["detail"].lower()
    assert "did not sign in" not in result["detail"], (
        "a browser that never started is not a wrong password"
    )


def test_any_unexpected_failure_still_answers_the_page(session, workspace, demo_server):
    _existing_account(workspace, demo_server)

    def explode(_self, _form):
        raise ValueError("something nobody predicted")

    original = webui.SetupSession.fill_existing
    webui.SetupSession.fill_existing = explode
    try:
        data = urllib.parse.urlencode({"t": session.token, "alias": "THEIR_USER"}).encode()
        req = urllib.request.Request(f"{session.url.split('?')[0]}fill", data)
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
        assert body["state"] == "failed"
        assert "something nobody predicted" in body["detail"]
    finally:
        webui.SetupSession.fill_existing = original
