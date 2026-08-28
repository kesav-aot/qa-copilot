from qa_copilot.secrets.base import (
    SecretAccessViolation,
    SecretProvider,
    SecretValue,
)
from qa_copilot.secrets.env import EnvSecretProvider
from qa_copilot.secrets.file_provider import FileSecretProvider

__all__ = [
    "SecretAccessViolation",
    "SecretProvider",
    "SecretValue",
    "EnvSecretProvider",
    "FileSecretProvider",
    "build_provider",
]


def build_provider(kind: str, **kwargs) -> SecretProvider:
    """Factory used by config loading. Add Vault/AWS/GCP backends here."""
    if kind == "env":
        return EnvSecretProvider(**kwargs)
    if kind == "file":
        return FileSecretProvider(**kwargs)
    raise ValueError(f"unknown secret provider {kind!r}")
