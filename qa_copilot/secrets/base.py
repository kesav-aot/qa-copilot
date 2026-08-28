"""Secret broker interfaces.

Design rule: a resolved secret is wrapped in :class:`SecretValue`, which refuses
to render itself. The only way to obtain the plaintext is ``reveal()``, and the
only callers permitted to invoke it are inside ``qa_copilot.executor`` — the code
that types the value into a browser or attaches it to an HTTP request. Nothing on
the MCP response path ever touches ``reveal()``.
"""

from __future__ import annotations

import abc
import inspect
from dataclasses import dataclass, field

from qa_copilot.sanitize import sanitizer

# Modules allowed to call SecretValue.reveal(). Anything else raises.
_TRUSTED_REVEAL_MODULES = ("qa_copilot.executor.",)


class SecretAccessViolation(RuntimeError):
    """Raised when untrusted code tries to unwrap a secret."""


@dataclass(frozen=True)
class SecretValue:
    """Opaque holder for a resolved secret.

    ``str``/``repr``/format/JSON all yield the alias, never the value, so an
    accidental log line or tool response leaks nothing.
    """

    alias: str
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        sanitizer.registry().register(self._value)

    def reveal(self) -> str:
        caller = inspect.stack()[1]
        module = caller.frame.f_globals.get("__name__", "")
        if not module.startswith(_TRUSTED_REVEAL_MODULES):
            raise SecretAccessViolation(
                f"module {module!r} is not permitted to reveal secret {self.alias!r}"
            )
        return self._value

    # --- rendering is always redacted -------------------------------------
    def __str__(self) -> str:
        return self.alias if "://" in self.alias else f"secret://{self.alias}"

    def __repr__(self) -> str:
        return f"SecretValue(alias={self.alias!r}, value=<redacted>)"

    def __format__(self, spec: str) -> str:
        return str(self)

    def __len__(self) -> int:
        return len(self._value)


class SecretProvider(abc.ABC):
    """Pluggable backend: env vars, encrypted file, Vault, AWS/Azure/GCP."""

    name: str = "abstract"

    @abc.abstractmethod
    def get(self, ref: str) -> str:
        """Return plaintext for a ``secret://`` reference, or raise KeyError."""

    @abc.abstractmethod
    def has(self, ref: str) -> bool:
        ...

    def resolve(self, ref: str) -> SecretValue:
        return SecretValue(alias=ref, _value=self.get(ref))
