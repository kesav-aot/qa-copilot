"""Point QA Copilot at the app the QA engineer named in Claude Desktop's settings.

This is `qa-copilot init` with the questions answered ahead of time. It runs the
same wizard — the same browser-based discovery of the sign-in form, the same
sign-in check before anything is written — rather than a second, unproven copy
of that logic. The wizard already accepts injectable readers for exactly this.

The password arrives in the environment, put there by Claude Desktop out of the
operating system's credential store. It goes straight into the secret store and
is never printed, returned, or written anywhere else.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def _clean(name: str, default: str = "") -> str:
    """Read an env var, treating an unsubstituted ${user_config.x} as unset."""
    value = os.environ.get(name, "") or ""
    if "${" in value:
        return default
    return value.strip() or default


def _free_account_name(config_dir: Path, account: str, env_name: str) -> str:
    """Pick an account name whose alias is not already taken.

    A fresh workspace ships the demo accounts, which already own ADMIN_USER — so
    the most natural answer a QA engineer can give ('admin') is exactly the one
    that collides. Rather than refusing, fall back to an environment-qualified
    name: 'admin' on the 'local' environment becomes LOCAL_ADMIN_USER.
    """
    import yaml

    path = config_dir / "identities.yaml"
    existing: dict = {}
    if path.is_file():
        existing = (yaml.safe_load(path.read_text()) or {}).get("identities") or {}

    def alias_for(name: str) -> str:
        upper = name.upper().replace("-", "_")
        return upper if upper.endswith("USER") else upper + "_USER"

    if alias_for(account) not in existing:
        return account
    qualified = f"{env_name}_{account}"
    if alias_for(qualified) not in existing:
        return qualified
    n = 2
    while alias_for(f"{qualified}_{n}") in existing:
        n += 1
    return f"{qualified}_{n}"


class ScriptedReader:
    """Answers the wizard's remaining prompts in the order it asks them."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)

    def __call__(self, _prompt: str = "") -> str:
        if not self._answers:
            raise RuntimeError(
                "the setup wizard asked more questions than this bundle knows how "
                "to answer; run `qa-copilot init` in a terminal instead"
            )
        return self._answers.pop(0)


def main() -> int:
    home = Path(_clean("QA_COPILOT_HOME") or (Path.home() / ".qa-copilot"))
    config_dir = Path(_clean("QA_COPILOT_CONFIG") or (home / "config"))

    app_url = _clean("QA_COPILOT_APP_URL")
    if not app_url:
        print("No app address configured; leaving the demo setup in place.")
        return 0

    username = _clean("QA_COPILOT_USERNAME")
    password = os.environ.get("QA_COPILOT_PASSWORD", "") or ""
    if "${" in password:
        password = ""
    if not username or not password:
        print(
            "An app address was given but the test account is incomplete.\n"
            "Fill in both the username and the password in Claude Desktop's\n"
            "settings for this extension, then restart Claude Desktop."
        )
        return 1

    env_name = _clean("QA_COPILOT_ENV_NAME", "local")
    account = _free_account_name(
        config_dir, _clean("QA_COPILOT_ACCOUNT", "admin"), env_name
    )

    # The wizard asks these four, in this order, once the address, environment
    # name and login path are supplied up front.
    reader = ScriptedReader(
        [
            "y",                                                   # discovery looks right?
            account,                                               # short account name
            _clean("QA_COPILOT_CAPABILITIES", "browse"),           # capabilities
            username,                                              # username
        ]
    )

    from qa_copilot.setup.wizard import run_wizard

    return asyncio.run(
        run_wizard(
            config_dir=config_dir,
            url=app_url,
            environment=env_name,
            login_path=_clean("QA_COPILOT_LOGIN_PATH", "/login"),
            headless=True,
            secrets_file=home / ".env",
            work_dir=home / "tests",
            reader=reader,
            password_reader=lambda _prompt="": password,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
