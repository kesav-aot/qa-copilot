"""Encrypted-at-rest local secret file provider.

Intended for a QA engineer's workstation: secrets live in a Fernet-encrypted
YAML blob unlocked by ``QA_COPILOT_VAULT_KEY``. Falls back to a clear warning
rather than silently reading plaintext.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from qa_copilot.secrets.base import SecretProvider


class FileSecretProvider(SecretProvider):
    name = "file"

    def __init__(self, path: Path, key_env: str = "QA_COPILOT_VAULT_KEY") -> None:
        self.path = path
        self.key_env = key_env
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.is_file():
            self._data = {}
            return self._data
        raw = self.path.read_bytes()
        key = os.environ.get(self.key_env)
        if key:
            try:
                from cryptography.fernet import Fernet
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "cryptography is required to read an encrypted secret file"
                ) from exc
            raw = Fernet(key.encode()).decrypt(raw)
        elif raw.lstrip().startswith(b"gAAAAA"):
            raise RuntimeError(
                f"{self.path} looks encrypted but {self.key_env} is not set"
            )
        self._data = yaml.safe_load(raw) or {}
        return self._data

    @staticmethod
    def _walk(data: dict[str, Any], ref: str) -> Any:
        node: Any = data
        for part in ref.removeprefix("secret://").strip("/").split("/"):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(ref)
            node = node[part]
        return node

    def has(self, ref: str) -> bool:
        try:
            self._walk(self._load(), ref)
        except KeyError:
            return False
        return True

    def get(self, ref: str) -> str:
        value = self._walk(self._load(), ref)
        if not isinstance(value, str):
            raise KeyError(f"secret reference {ref!r} does not point at a string")
        return value
