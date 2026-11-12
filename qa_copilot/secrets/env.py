"""Environment-variable secret provider (MVP default).

``secret://qa/admin/password`` maps to env var ``QA_SECRET__QA__ADMIN__PASSWORD``.
A ``.env`` file in the project root is loaded if present so local runs work
without exporting anything into the shell history.
"""

from __future__ import annotations

import os
from pathlib import Path

from qa_copilot.secrets.base import SecretProvider

PREFIX = "QA_SECRET__"


def ref_to_env_var(ref: str) -> str:
    path = ref.removeprefix("secret://").strip("/")
    return PREFIX + path.replace("/", "__").replace("-", "_").upper()


# Keys this process took from a .env file. Those may be replaced when the file
# is read again - setup has just written a new password - while a variable that
# came from the real environment still wins over the file, as it always has.
_FROM_DOTENV: set[str] = set()


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key not in os.environ or key in _FROM_DOTENV:
            os.environ[key] = value
            _FROM_DOTENV.add(key)


class EnvSecretProvider(SecretProvider):
    name = "env"

    def __init__(self, dotenv: Path | None = None) -> None:
        if dotenv is not None:
            load_dotenv(dotenv)

    def has(self, ref: str) -> bool:
        return ref_to_env_var(ref) in os.environ

    def get(self, ref: str) -> str:
        var = ref_to_env_var(ref)
        try:
            return os.environ[var]
        except KeyError:
            raise KeyError(
                f"secret reference {ref!r} is not configured (expected env var {var})"
            ) from None
