"""Write configuration without destroying the comments already in the files.

Round-tripping YAML through PyYAML silently deletes every comment, and those
comments are how the next person understands the file. So new entries are
appended as text and the result is parsed to prove it is still valid.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigWriteError(RuntimeError):
    pass


def _indent(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def append_under_key(path: Path, top_key: str, entry_name: str, body: dict[str, Any]) -> None:
    """Add ``entry_name: body`` under ``top_key``, keeping the rest of the file.

    Parses the result and restores the original if anything went wrong, so a bad
    write can never leave the workspace unusable.
    """
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    parsed = yaml.safe_load(original) or {} if original.strip() else {}

    if isinstance(parsed, dict) and entry_name in (parsed.get(top_key) or {}):
        raise ConfigWriteError(
            f"{path} already has an entry called {entry_name!r}. "
            f"Remove it first, or choose a different name."
        )

    block = yaml.safe_dump({entry_name: body}, sort_keys=False, allow_unicode=True, width=100)
    addition = "\n" + _indent(block).rstrip() + "\n"

    updated = original if original.endswith("\n") or not original else original + "\n"
    if top_key not in parsed:
        updated += f"\n{top_key}:\n"
    updated += addition

    try:
        check = yaml.safe_load(updated) or {}
        if entry_name not in (check.get(top_key) or {}):
            raise ConfigWriteError("the new entry did not land under the right key")
    except Exception as exc:
        raise ConfigWriteError(
            f"writing to {path} would have produced invalid YAML ({exc}); nothing was changed"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


# Anything that is not a dotenv file is refused outright. A caller that mixes up
# two path variables must not be able to write a password into a config file
# that gets committed — this is checked structurally rather than trusted.
_SECRET_SAFE_SUFFIXES = {"", ".env", ".secrets"}


def append_secrets(path: Path, values: dict[str, str]) -> list[str]:
    """Append secret values to a .env file. Returns the variable names written —
    never the values, so a caller cannot log them by accident."""
    name = path.name
    if not (name.startswith(".env") or path.suffix in _SECRET_SAFE_SUFFIXES):
        raise ConfigWriteError(
            f"refusing to write secrets to {path} — secrets may only go to a "
            f".env file. This is a bug in the caller."
        )
    if path.suffix in {".yaml", ".yml", ".json", ".toml", ".ini", ".md"}:
        raise ConfigWriteError(
            f"refusing to write secrets to {path} — that is a configuration file, "
            f"and configuration files get committed."
        )
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = []
    for key, value in values.items():
        if f"{key}=" in existing:
            raise ConfigWriteError(
                f"{path} already defines {key}. Remove that line first if you want "
                f"to change it."
            )
        lines.append(f"{key}={value}")

    body = existing if existing.endswith("\n") or not existing else existing + "\n"
    body += "\n" + "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return list(values)


def allow_environment(settings_path: Path, name: str) -> bool:
    """Add an environment to the policy allow-list. Returns True if changed."""
    if not settings_path.is_file():
        return False
    text = settings_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    allowed = ((data.get("policy") or {}).get("allowed_environments")) or []
    if not allowed or name in allowed:
        return False

    import re

    updated, count = re.subn(
        r"(?m)^(\s*allowed_environments\s*:\s*)\[([^\]]*)\]",
        lambda m: f"{m.group(1)}[{m.group(2).strip().rstrip(',')}, {name}]",
        text,
        count=1,
    )
    if not count:
        return False
    settings_path.write_text(updated, encoding="utf-8")
    return True
