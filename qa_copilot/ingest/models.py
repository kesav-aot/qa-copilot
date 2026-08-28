"""Normalised representation of a manual test case.

Every ingest format collapses into :class:`ManualTestCase`. Downstream — the
analyser, the drafter, the model — only ever sees this shape, so adding a new
source format never touches anything but a parser.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceFormat = Literal["markdown", "csv", "excel", "gherkin", "jira", "text"]
Severity = Literal["info", "warning", "blocker"]
FindingKind = Literal["ambiguity", "gap", "risk", "security", "data"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualStep(Base):
    number: int
    action: str
    expected: str | None = None

    def text(self) -> str:
        return f"{self.action} {self.expected or ''}".strip()


class ManualTestCase(Base):
    id: str
    title: str
    source: str
    format: SourceFormat
    description: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    steps: list[ManualStep] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    priority: str | None = None

    def all_text(self) -> str:
        parts = [self.title, self.description or "", *self.preconditions, *self.expected_results]
        parts += [s.text() for s in self.steps]
        return "\n".join(p for p in parts if p)


class Finding(Base):
    """Something a human should look at before this becomes an automated test."""

    kind: FindingKind
    severity: Severity
    message: str
    location: str | None = None
    suggestion: str | None = None

    def to_line(self) -> str:
        where = f" [{self.location}]" if self.location else ""
        fix = f" → {self.suggestion}" if self.suggestion else ""
        return f"{self.severity.upper():8} {self.kind:9}{where} {self.message}{fix}"


class Analysis(Base):
    case_id: str
    findings: list[Finding] = Field(default_factory=list)
    suggested_capability: str | None = None
    suggested_identity: str | None = None
    inferred_risk: Literal["low", "medium", "high"] = "low"
    automatable: bool = True

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocker"]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return {
            "case_id": self.case_id,
            "automatable": self.automatable,
            "inferred_risk": self.inferred_risk,
            "suggested_identity": self.suggested_identity,
            "suggested_capability": self.suggested_capability,
            "counts": counts,
        }


class IngestResult(Base):
    cases: list[ManualTestCase] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    files_read: list[str] = Field(default_factory=list)
