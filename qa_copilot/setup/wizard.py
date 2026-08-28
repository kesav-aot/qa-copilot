"""`qa-copilot init` — point QA Copilot at an application without writing YAML.

It asks plain questions, opens a real browser to find the sign-in form, takes
the password without echoing it, proves the details work by signing in, and
writes the configuration itself.

The password goes straight from the prompt into the secret store. It is never
printed, never logged, and never returned from any function here.
"""

from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from qa_copilot.config import Environment, LoginRecipe
from qa_copilot.secrets.env import ref_to_env_var
from qa_copilot.setup.discover import discover_login, find_error_target
from qa_copilot.setup.writer import (
    ConfigWriteError,
    allow_environment,
    append_secrets,
    append_under_key,
)

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"

_SUGGESTED_CAPABILITIES = [
    ("browse", "look at pages that any signed-in user can see"),
    ("manage_users", "add, edit or disable other people's accounts"),
    ("manage_settings", "change application or account settings"),
    ("create_order", "place an order / submit the main thing this app does"),
]


@dataclass
class Answers:
    url: str
    environment: str
    login_path: str
    account: str
    capabilities: list[str]
    username: str


def _say(text: str = "") -> None:
    print(text)


def _ask(question: str, default: str | None = None, *, reader=input) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = reader(f"  {question}{suffix}\n  > ").strip()
        if answer:
            return answer
        if default is not None:
            return default
        _say("  (an answer is needed)")


def _ask_yes(question: str, default: bool = True, *, reader=input) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = reader(f"  {question} [{hint}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.strip().lower()).strip("_")


def _default_environment_name(url: str) -> str:
    host = (urlparse(url).hostname or "app").split(".")[0]
    for known in ("qa", "staging", "stage", "test", "uat", "dev", "sandbox"):
        if known in url.lower():
            return known
    return _slug(host) or "app"


async def run_wizard(
    *,
    config_dir: Path,
    url: str | None = None,
    environment: str | None = None,
    login_path: str | None = None,
    headless: bool = True,
    secrets_file: Path | None = None,
    work_dir: Path | None = None,
    reader=input,
    password_reader=getpass.getpass,
) -> int:
    secrets_file = secrets_file or Path(".env")
    work_dir = work_dir or Path.cwd()
    from qa_copilot.executor.browser import open_session

    _say()
    _say(f"{BOLD}Let's point QA Copilot at your application.{RESET}")
    _say(f"{DIM}  Nothing is written until the end, and your password is never shown.{RESET}")
    _say()

    url = url or _ask("What is the web address of the app you want to test?", reader=reader)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    environment = environment or _ask(
        "What should we call this environment?", _default_environment_name(url), reader=reader
    )
    environment = _slug(environment)

    environments_file = config_dir / "environments.yaml"
    identities_file = config_dir / "identities.yaml"

    def _existing(path: Path, key: str) -> dict:
        import yaml

        if not path.is_file():
            return {}
        return (yaml.safe_load(path.read_text()) or {}).get(key) or {}

    if environment in _existing(environments_file, "environments"):
        _say(f"{RED}  There is already an environment called {environment!r}.{RESET}")
        _say("  Remove it from config/environments.yaml first, or pick another name.")
        return 2

    login_path = login_path or _ask(
        "Where is the sign-in page?", urlparse(url).path or "/login", reader=reader
    )
    if not login_path.startswith("/"):
        login_path = "/" + login_path

    # --- look at the page -------------------------------------------------
    _say()
    _say(f"  Opening a browser to look at {base}{login_path} …")
    probe_env = Environment(name=environment, base_url=base)
    session = await open_session(probe_env, Path("artifacts"), headless=headless)
    try:
        found = await discover_login(session.page, base + login_path)

        if not found.complete:
            _say(f"{RED}  I could not work out the sign-in form.{RESET}")
            for problem in found.problems:
                _say(f"    {problem}")
            if found.inventory:
                from qa_copilot.executor.resolver import format_inventory

                _say()
                _say("  Here is what I did find on that page:")
                _say(format_inventory(found.inventory))
            return 2

        _say(f"{GREEN}  Found the sign-in form:{RESET}")
        _say(f"    username field  →  {found.username.describe()}")
        _say(f"    password field  →  {found.password.describe()}")
        _say(f"    sign-in button  →  {found.submit.describe()}")
        _say()
        if not _ask_yes("Does that look right?", reader=reader):
            _say("  Nothing was written. Give me the address of the form itself and try again.")
            return 1

        # --- the account ---------------------------------------------------
        _say()
        _say(f"{BOLD}  Now a test account to sign in with.{RESET}")
        account = _slug(
            _ask("What should we call it? (a short name, like admin)", "admin", reader=reader)
        )
        alias = account.upper() + ("" if account.upper().endswith("USER") else "_USER")

        # Check this before anything is written. The old order wrote the secrets
        # and the environment first and only then discovered the clash, leaving
        # a half-configured workspace behind.
        if alias in _existing(identities_file, "identities"):
            _say()
            _say(f"{RED}  There is already an account called {alias!r}.{RESET}")
            _say(f"  Pick a different name than {account!r}, or remove that entry from")
            _say(f"  {identities_file}.")
            _say(f"{DIM}  Nothing was written.{RESET}")
            return 2

        _say()
        _say("  What should this account be able to do? Tests will ask for accounts")
        _say("  by these words instead of by name, so they keep working when")
        _say("  accounts get renamed.")
        for name, meaning in _SUGGESTED_CAPABILITIES:
            _say(f"      {name:16} {DIM}{meaning}{RESET}")
        capabilities = [
            _slug(c)
            for c in re.split(r"[,\s]+", _ask("Which ones? (comma separated)", "browse", reader=reader))
            if c.strip()
        ]

        _say()
        username = _ask(f"Username or email for the {account} account?", reader=reader)
        password = password_reader("  Password? (not shown, not saved to your shell history)\n  > ")
        if not password:
            _say(f"{RED}  No password given; nothing was written.{RESET}")
            return 2

        # --- prove it works ---------------------------------------------------
        _say()
        _say("  Checking those details work …")
        await session.page.goto(base + login_path, wait_until="domcontentloaded")
        from qa_copilot.executor.resolver import resolve

        await (await resolve(session.page, found.username.target)).first.fill(username)
        await (await resolve(session.page, found.password.target)).first.fill(password)
        await (await resolve(session.page, found.submit.target)).first.click()
        try:
            await session.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        landed = urlparse(session.page.url).path or "/"
        error_target = await find_error_target(session.page)

        if landed.rstrip("/") == login_path.rstrip("/") or error_target is not None:
            _say(f"{RED}  Signing in did not work — I am still on the sign-in page.{RESET}")
            if error_target is not None:
                _say(f"    The page shows an error: {error_target.summary()}")
            _say("    Check the username and password, then run `qa-copilot init` again.")
            _say(f"{DIM}    Nothing was written.{RESET}")
            return 1

        _say(f"{GREEN}  Signed in. Landed on {landed}{RESET}")

        # A failed sign-in should fail fast later, so find the error element too.
        await session.page.goto(base + login_path, wait_until="domcontentloaded")
        await (await resolve(session.page, found.username.target)).first.fill(username)
        await (await resolve(session.page, found.password.target)).first.fill(
            "deliberately-wrong-" + "x" * 8
        )
        await (await resolve(session.page, found.submit.target)).first.click()
        try:
            await session.page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        failure_target = await find_error_target(session.page)
        if failure_target is not None:
            _say(f"{DIM}  (a wrong password shows: {failure_target.summary()}){RESET}")
    finally:
        await session.close()

    # --- write it -----------------------------------------------------------
    recipe = LoginRecipe(
        path=login_path,
        username_target=found.username.target,
        password_target=found.password.target,
        submit_target=found.submit.target,
        success_url_contains=landed if landed != "/" else None,
        failure_target=failure_target,
    )
    env_body = {
        "base_url": base,
        "login": recipe.model_dump(mode="json", exclude_none=True),
    }
    user_ref = f"secret://{environment}/{account}/username"
    pass_ref = f"secret://{environment}/{account}/password"
    identity_body = {
        "description": f"Test account for {environment} ({account}).",
        "capabilities": capabilities,
        "username_ref": user_ref,
        "password_ref": pass_ref,
        "environments": [environment],
    }

    try:
        append_secrets(
            secrets_file,
            {ref_to_env_var(user_ref): username, ref_to_env_var(pass_ref): password},
        )
        append_under_key(environments_file, "environments", environment, env_body)
        append_under_key(identities_file, "identities", alias, identity_body)
    except ConfigWriteError as exc:
        _say(f"{RED}  {exc}{RESET}")
        return 2

    changed = allow_environment(config_dir / "settings.yaml", environment)

    _say()
    _say(f"{GREEN}{BOLD}  Done.{RESET}")
    _say(f"    wrote  {environments_file}   (how to sign in)")
    _say(f"    wrote  {identities_file}     (the {alias} account)")
    _say(f"    wrote  {secrets_file}                  (the password, kept out of git)")
    if changed:
        _say(f"    added  {environment!r} to the policy allow-list")

    starter = work_dir / "my-first-test.txt"
    if not starter.exists():
        starter.write_text(
            f"# My first test\n"
            f"Environment: {environment}\n"
            f"\n"
            f"Log in as {alias}\n"
            f"Check the URL contains {landed}\n"
            f"Take a screenshot called \"signed in\"\n",
            encoding="utf-8",
        )
        _say(f"    wrote  {starter}                   (a test you can run right now)")

    _say()
    _say(f"{BOLD}  Try it:{RESET}")
    _say(f"      qa-copilot check {starter}")
    _say(f"      qa-copilot run {starter} --headed")
    _say()
    _say(f"{DIM}  Then read docs/WRITING-TESTS.md to write your own.{RESET}")
    return 0
