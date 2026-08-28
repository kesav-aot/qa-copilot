"""The Test DSL — the stable intermediate representation between AI intent and
concrete execution.

The AI never emits Playwright code. It emits this. Adapters (Playwright today,
Selenium/Appium/API later) decide how to run it. Two properties matter:

* **No secret values.** ``authenticate`` names an identity alias or a required
  capability; it can never carry a username or password. Validation rejects any
  step whose literal text looks like a credential.
* **Declarative targets.** Steps address elements by role/label/testid so the
  plan survives selector churn.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(Base):
    """How to find an element.

    The precise fields (``testid``, ``role`` + ``name``, ``label``, ``text``,
    ``css``) bind at author time. ``describe`` binds at *run* time instead: it
    holds an ordinary phrase like ``the Save button`` or ``"Disable" in the Rae
    Rivera row``, and the resolver works out what that means against the live
    page. That is what lets someone write a test without knowing what a selector
    is — and it is why a failure can answer "here is what I did find" rather than
    "no element matched".
    """

    describe: str | None = Field(
        default=None,
        description='Plain-English element description, e.g. "the Save button".',
    )
    within: str | None = Field(
        default=None,
        description='Scope the search to the row/section containing this text.',
    )
    index: int | None = Field(
        default=None,
        ge=1,
        description="1-based: which match to use when a phrase matches several.",
    )

    testid: str | None = None
    role: str | None = None
    name: str | None = None
    label: str | None = None
    text: str | None = None
    css: str | None = Field(default=None, description="Escape hatch; discouraged.")

    @model_validator(mode="after")
    def _at_least_one(self) -> "Target":
        if not any(
            [self.describe, self.testid, self.role, self.name, self.label, self.text, self.css]
        ):
            raise ValueError(
                "target must say how to find the element — a plain-English "
                "'describe', or one of testid/role/label/text/css"
            )
        if (self.within or self.index) and not self.describe:
            raise ValueError("'within' and 'index' only apply to a 'describe' target")
        return self

    def summary(self) -> str:
        """A short human-readable form, used in reports and audit entries."""
        if self.describe:
            out = self.describe
            if self.within:
                out += f" in the {self.within!r} row"
            if self.index:
                out += f" (match {self.index})"
            return out
        parts = [f"{k}={v!r}" for k, v in self.model_dump(exclude_none=True).items()]
        return " ".join(parts) or "<empty target>"


# --- steps ----------------------------------------------------------------

_CREDENTIAL_SHAPED = re.compile(
    r"(?i)(password|passwd|pwd|secret|api[_-]?key|token|bearer\s|-----BEGIN)"
)


class Authenticate(Base):
    action: Literal["authenticate"]
    identity: str | None = Field(
        default=None, description="Identity alias, e.g. ADMIN_USER."
    )
    capability: str | None = Field(
        default=None,
        description="Ask the broker to pick any identity holding this capability.",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "Authenticate":
        if bool(self.identity) == bool(self.capability):
            raise ValueError("authenticate requires exactly one of identity/capability")
        return self


class Navigate(Base):
    action: Literal["navigate"]
    path: str = Field(description="Path relative to the environment base URL.")


class Click(Base):
    action: Literal["click"]
    target: Target


class DoubleClick(Base):
    """Some grids only respond to dblclick; a single click silently does nothing."""

    action: Literal["double_click"]
    target: Target


class Fill(Base):
    action: Literal["fill"]
    target: Target
    value: str = Field(description="Non-secret literal text only.")

    @field_validator("value")
    @classmethod
    def _reject_credentials(cls, v: str) -> str:
        if _CREDENTIAL_SHAPED.search(v):
            raise ValueError(
                "fill.value looks like a credential; use the authenticate step "
                "or fill_secret with a secret alias instead"
            )
        return v


class FillSecret(Base):
    """Escape hatch for forms the canned ``authenticate`` recipe cannot drive.

    The plan carries only the alias. The executor resolves and types the value;
    the field is masked in every artifact afterwards.
    """

    action: Literal["fill_secret"]
    target: Target
    secret_ref: str = Field(pattern=r"^secret://[\w\-/]+$")


class Select(Base):
    action: Literal["select"]
    target: Target
    option: str


class WaitFor(Base):
    action: Literal["wait_for"]
    target: Target | None = None
    url_contains: str | None = None
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)


class Pause(Base):
    """A fixed wait. Discouraged — ``wait_for`` an observable thing instead — but
    people write it, and refusing would just push them out of the tool."""

    action: Literal["pause"]
    seconds: int = Field(ge=1, le=30)


class ApiRequest(Base):
    action: Literal["api_request"]
    identity: str | None = None
    capability: str | None = Field(
        default=None,
        description="Instead of an alias: let the broker pick who makes the call.",
    )
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    path: str
    json_body: dict[str, Any] | None = None
    expect_status: int | None = None

    @model_validator(mode="after")
    def _not_both(self) -> "ApiRequest":
        if self.identity and self.capability:
            raise ValueError("api_request takes an identity or a capability, not both")
        return self


class Screenshot(Base):
    action: Literal["screenshot"]
    name: str = "screenshot"


class Assert(Base):
    action: Literal["assert"]
    kind: Literal["visible", "not_visible", "text", "url_contains", "status"]
    target: Target | None = None
    expected: str | int | None = None

    @model_validator(mode="after")
    def _needs_operand(self) -> "Assert":
        if self.kind in {"visible", "not_visible"} and self.target is None:
            raise ValueError(f"assert kind {self.kind!r} requires a target")
        if self.kind in {"text", "url_contains", "status"} and self.expected is None:
            raise ValueError(f"assert kind {self.kind!r} requires an expected value")
        return self


Step = Annotated[
    Union[
        Authenticate,
        Navigate,
        Click,
        DoubleClick,
        Fill,
        FillSecret,
        Select,
        WaitFor,
        Pause,
        ApiRequest,
        Screenshot,
        Assert,
    ],
    Field(discriminator="action"),
]


class TestPlan(Base):
    version: Literal[1] = 1
    name: str
    description: str | None = None
    environment: str
    risk: Risk = Risk.LOW
    tags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _bounded(cls, v: list[Step]) -> list[Step]:
        if len(v) > 200:
            raise ValueError("plan exceeds 200 steps")
        return v


def json_schema() -> dict[str, Any]:
    return TestPlan.model_json_schema()
