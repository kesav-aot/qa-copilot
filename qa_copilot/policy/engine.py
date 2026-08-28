"""Policy engine — the gate between an AI-authored plan and real execution.

Deliberately dumb and deterministic. It runs *outside* the model: no prompt can
argue with it, and there is no MCP tool that grants approval. A human approves
via the CLI, which writes a signed-by-possession approval file the engine reads.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from qa_copilot.config import PolicyConfig
from qa_copilot.dsl.schema import Risk, TestPlan

_RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}

# Only steps that *act* can be destructive. Scanning an assertion for the word
# "delete" flags every test that checks a Delete button is absent — which is
# exactly the authorization test you most want running unattended.
_INERT_ACTIONS = frozenset(
    {"assert", "screenshot", "wait_for", "pause", "authenticate"}
)


@dataclass
class Decision:
    allowed: bool
    risk: Risk
    requires_approval: bool
    approved: bool
    fingerprint: str
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_execute(self) -> bool:
        return self.allowed and (self.approved or not self.requires_approval)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "risk": str(self.risk),
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "can_execute": self.can_execute,
            "fingerprint": self.fingerprint,
            "violations": self.violations,
            "warnings": self.warnings,
        }


def fingerprint(plan: TestPlan) -> str:
    canonical = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


class ApprovalStore:
    """Filesystem approval records. Written by the CLI, read by the engine."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, fp: str) -> Path:
        return self.root / f"{fp}.json"

    def is_approved(self, fp: str) -> bool:
        return self._path(fp).is_file()

    def approve(self, fp: str, *, approver: str, note: str = "") -> dict:
        record = {
            "fingerprint": fp,
            "approver": approver,
            "note": note,
            "approved_at": datetime.now(UTC).isoformat(),
        }
        self._path(fp).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    def revoke(self, fp: str) -> bool:
        path = self._path(fp)
        if path.is_file():
            path.unlink()
            return True
        return False

    def get(self, fp: str) -> dict | None:
        path = self._path(fp)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


class PolicyEngine:
    def __init__(self, config: PolicyConfig, approvals: ApprovalStore) -> None:
        self.config = config
        self.approvals = approvals

    # --- risk -------------------------------------------------------------
    def classify(self, plan: TestPlan) -> Risk:
        """Take the higher of the author's declared risk and what we infer."""
        inferred = Risk.LOW
        words = self.config.destructive_actions

        def bump(level: Risk) -> None:
            nonlocal inferred
            if _RISK_ORDER[level] > _RISK_ORDER[inferred]:
                inferred = level

        for step in plan.steps:
            action = step.action
            blob = json.dumps(step.model_dump(mode="json")).lower()
            if action not in _INERT_ACTIONS and any(w in blob for w in words):
                bump(Risk.MEDIUM)
            if action == "api_request" and step.method in {"DELETE", "PUT", "PATCH"}:
                bump(Risk.MEDIUM)
            if action == "api_request" and step.method == "DELETE":
                bump(Risk.HIGH)
            if action == "fill_secret":
                bump(Risk.MEDIUM)

        return inferred if _RISK_ORDER[inferred] > _RISK_ORDER[plan.risk] else plan.risk

    # --- evaluation -------------------------------------------------------
    def evaluate(self, plan: TestPlan) -> Decision:
        violations: list[str] = []
        warnings: list[str] = []

        env = plan.environment
        if env in self.config.blocked_environments:
            violations.append(f"environment {env!r} is blocked by policy")
        if self.config.allowed_environments and env not in self.config.allowed_environments:
            violations.append(
                f"environment {env!r} is not in the allow-list "
                f"{self.config.allowed_environments}"
            )
        if len(plan.steps) > self.config.max_steps:
            violations.append(
                f"plan has {len(plan.steps)} steps, policy limit is {self.config.max_steps}"
            )

        if not self.config.allow_css_selectors:
            for i, step in enumerate(plan.steps):
                target = getattr(step, "target", None)
                if target is not None and target.css:
                    violations.append(f"step {i} uses a raw CSS selector, which policy forbids")
        else:
            for i, step in enumerate(plan.steps):
                target = getattr(step, "target", None)
                if target is not None and target.css:
                    warnings.append(
                        f"step {i} uses a raw CSS selector; prefer role/label/testid"
                    )

        if not any(s.action == "assert" for s in plan.steps):
            warnings.append("plan contains no assertions — it can pass without verifying anything")

        risk = self.classify(plan)
        threshold = Risk(self.config.approval_required_risk)
        requires_approval = _RISK_ORDER[risk] >= _RISK_ORDER[threshold]
        fp = fingerprint(plan)

        return Decision(
            allowed=not violations,
            risk=risk,
            requires_approval=requires_approval,
            approved=self.approvals.is_approved(fp),
            fingerprint=fp,
            violations=violations,
            warnings=warnings,
        )
