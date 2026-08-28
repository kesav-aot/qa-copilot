"""Configuration loading for environments, identities and policy.

These files are read by the trusted layer only. Note what the AI is *allowed* to
see, via ``Identity.public_view()`` and ``Environment.public_view()``: aliases,
capabilities, base URLs. Never a ``secret://`` reference, never a credential.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from qa_copilot.dsl.schema import Target


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRecipe(Base):
    """How the trusted executor performs a UI login for an environment.

    Lives in config rather than in the plan so the AI never chooses how a
    credential is typed.
    """

    path: str = "/login"
    username_target: Target
    password_target: Target
    submit_target: Target
    extra_targets: dict[str, Target] = Field(
        default_factory=dict,
        description=(
            "Further credential fields this form demands, keyed by a short name "
            "such as 'pin'. Each identity supplies the value through a matching "
            "entry in its extra_refs. Here rather than in the plan, for the same "
            "reason as the other two: the AI does not choose how a credential is "
            "typed."
        ),
    )
    success_url_contains: str | None = None
    success_target: Target | None = None
    failure_target: Target | None = None


class ApiAuth(Base):
    mode: str = Field(default="bearer", pattern=r"^(bearer|header|basic|none)$")
    header_name: str = "Authorization"
    token_ref: str | None = None
    login_path: str | None = Field(
        default=None,
        description="If set, exchange username/password here for a token.",
    )


class Environment(Base):
    name: str
    base_url: str
    api_base_url: str | None = None
    login: LoginRecipe | None = None
    api_auth: ApiAuth = Field(default_factory=ApiAuth)
    verify_tls: bool = Field(
        default=True,
        description=(
            "Verify TLS certificates. Turn off only for a QA environment with a "
            "self-signed certificate, and know that doing so exposes the "
            "credentials this tool injects to an active MITM."
        ),
    )

    def public_view(self) -> dict[str, Any]:
        return {"name": self.name, "base_url": self.base_url}


class Identity(Base):
    alias: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    username_ref: str | None = None
    password_ref: str | None = None
    extra_refs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "secret:// references for the environment's extra login fields, "
            "keyed by the same short name the login recipe uses."
        ),
    )
    api_token_ref: str | None = None
    environments: list[str] = Field(default_factory=list)

    def available_in(self, env: str) -> bool:
        return not self.environments or env in self.environments

    def public_view(self) -> dict[str, Any]:
        """Exactly what the model is permitted to know about an identity."""
        return {
            "alias": self.alias,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "environments": self.environments or ["*"],
        }


class PolicyConfig(Base):
    allowed_environments: list[str] = Field(default_factory=list)
    blocked_environments: list[str] = Field(default_factory=lambda: ["production", "prod"])
    approval_required_risk: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    destructive_actions: list[str] = Field(
        default_factory=lambda: ["delete", "disable", "purge", "drop", "revoke", "deactivate"]
    )
    max_steps: int = 100
    allow_css_selectors: bool = True


class SecretsConfig(Base):
    provider: str = "env"
    file_path: str | None = None
    dotenv: str | None = ".env"


class Config(Base):
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    environments: dict[str, Environment] = Field(default_factory=dict)
    identities: dict[str, Identity] = Field(default_factory=dict)
    artifact_dir: str = "artifacts"
    plan_dir: str = "plans"
    testcase_dir: str = "testcases"

    root: Path = Field(
        default_factory=Path.cwd,
        description=(
            "Directory that relative paths in this config are resolved against. "
            "Set to the parent of the config directory at load time. Without this, "
            "an agent host that launches the server from / — Claude Desktop does — "
            "would resolve 'plans' and '.env' against the wrong place and find "
            "nothing."
        ),
    )

    def _resolve(self, value: str | None) -> Path | None:
        """Absolute values are used as given; relative ones hang off ``root``."""
        if value is None:
            return None
        return (self.root / value).resolve()

    @property
    def artifacts_path(self) -> Path:
        return self._resolve(self.artifact_dir)  # type: ignore[return-value]

    @property
    def plans_path(self) -> Path:
        return self._resolve(self.plan_dir)  # type: ignore[return-value]

    @property
    def testcases_path(self) -> Path:
        return self._resolve(self.testcase_dir)  # type: ignore[return-value]

    @property
    def dotenv_path(self) -> Path | None:
        return self._resolve(self.secrets.dotenv)

    @property
    def secret_file_path(self) -> Path | None:
        return self._resolve(self.secrets.file_path)

    def environment(self, name: str) -> Environment:
        try:
            return self.environments[name]
        except KeyError:
            known = ", ".join(sorted(self.environments)) or "<none configured>"
            raise KeyError(f"unknown environment {name!r}; configured: {known}") from None

    def identity(self, alias: str) -> Identity:
        try:
            return self.identities[alias]
        except KeyError:
            known = ", ".join(sorted(self.identities)) or "<none configured>"
            raise KeyError(f"unknown identity {alias!r}; configured: {known}") from None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def config_dir() -> Path:
    return Path(os.environ.get("QA_COPILOT_CONFIG", "config")).expanduser().resolve()


def load_config(directory: Path | None = None) -> Config:
    d = directory or config_dir()
    envs_raw = _read_yaml(d / "environments.yaml").get("environments", {})
    ids_raw = _read_yaml(d / "identities.yaml").get("identities", {})
    settings = _read_yaml(d / "settings.yaml")

    environments = {
        name: Environment(name=name, **body) for name, body in envs_raw.items()
    }
    identities = {
        alias: Identity(alias=alias, **body) for alias, body in ids_raw.items()
    }
    return Config(
        environments=environments,
        identities=identities,
        secrets=SecretsConfig(**settings.get("secrets", {})),
        policy=PolicyConfig(**settings.get("policy", {})),
        artifact_dir=settings.get("artifact_dir", "artifacts"),
        plan_dir=settings.get("plan_dir", "plans"),
        testcase_dir=settings.get("testcase_dir", "testcases"),
        root=d.parent,
    )
