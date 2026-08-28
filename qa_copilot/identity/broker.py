"""Identity broker.

Translates the two things the AI is allowed to express — "log in as ADMIN_USER"
or "I need someone who can manage_settings" — into resolved credentials that only
the executor ever unwraps.
"""

from __future__ import annotations

from dataclasses import dataclass

from qa_copilot.config import Config, Identity
from qa_copilot.secrets.base import SecretProvider, SecretValue


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    identity: str
    username: SecretValue
    password: SecretValue


class IdentityBroker:
    def __init__(self, config: Config, provider: SecretProvider) -> None:
        self.config = config
        self.provider = provider

    # --- what the model may see ------------------------------------------
    def list_identities(self, environment: str | None = None) -> list[dict]:
        out = []
        for identity in sorted(self.config.identities.values(), key=lambda i: i.alias):
            if environment and not identity.available_in(environment):
                continue
            view = identity.public_view()
            view["credentials_configured"] = self._configured(identity)
            out.append(view)
        return out

    def list_capabilities(self) -> list[str]:
        caps: set[str] = set()
        for identity in self.config.identities.values():
            caps.update(identity.capabilities)
        return sorted(caps)

    def _configured(self, identity: Identity) -> bool:
        refs = [identity.username_ref, identity.password_ref]
        return all(r and self.provider.has(r) for r in refs)

    # --- resolution -------------------------------------------------------
    def select(self, capability: str, environment: str) -> Identity:
        matches = [
            i
            for i in self.config.identities.values()
            if capability in i.capabilities and i.available_in(environment)
        ]
        if not matches:
            available = ", ".join(self.list_capabilities()) or "<none>"
            raise IdentityError(
                f"no identity in {environment!r} has capability {capability!r}; "
                f"available capabilities: {available}"
            )
        # Deterministic: fewest extra capabilities wins (least privilege), then alias.
        matches.sort(key=lambda i: (len(i.capabilities), i.alias))
        return matches[0]

    def resolve(self, *, identity: str | None, capability: str | None, environment: str) -> Identity:
        if identity:
            resolved = self.config.identity(identity)
            if not resolved.available_in(environment):
                raise IdentityError(
                    f"identity {identity!r} is not available in environment {environment!r}"
                )
            return resolved
        if capability:
            return self.select(capability, environment)
        raise IdentityError("resolve requires an identity alias or a capability")

    def credentials(self, identity: Identity) -> Credentials:
        if not identity.username_ref or not identity.password_ref:
            raise IdentityError(
                f"identity {identity.alias!r} has no username/password references configured"
            )
        for ref in (identity.username_ref, identity.password_ref):
            if not self.provider.has(ref):
                raise IdentityError(
                    f"secret {ref!r} for identity {identity.alias!r} is not available "
                    f"from the {self.provider.name!r} provider"
                )
        return Credentials(
            identity=identity.alias,
            username=self.provider.resolve(identity.username_ref),
            password=self.provider.resolve(identity.password_ref),
        )

    def api_token(self, identity: Identity) -> SecretValue | None:
        if identity.api_token_ref and self.provider.has(identity.api_token_ref):
            return self.provider.resolve(identity.api_token_ref)
        return None

    def resolve_secret(self, ref: str) -> SecretValue:
        if not self.provider.has(ref):
            raise IdentityError(f"secret reference {ref!r} is not available")
        return self.provider.resolve(ref)
