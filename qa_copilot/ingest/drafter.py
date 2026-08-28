"""Draft a Test DSL plan from a manual test case.

This is a **scaffold, not a translation**. It maps the step phrasings it can
recognise with high confidence and refuses to guess at the rest, listing them as
TODOs for the model or the human to resolve. A drafter that silently invented a
selector would be worse than one that produced nothing, because the resulting
plan would look reviewed when it was not.
"""

from __future__ import annotations

import re
from typing import Any

from qa_copilot.dsl.schema import TestPlan
from qa_copilot.identity.broker import IdentityBroker
from qa_copilot.ingest.analyzer import analyze
from qa_copilot.ingest.models import Analysis, ManualStep, ManualTestCase
from qa_copilot.plain.writer import to_plain_english
from qa_copilot.sanitize import sanitizer

# Double and smart quotes are unambiguous. A straight single quote is only
# treated as a quote when it does not follow a word character, so "the user's
# status is 'disabled'" yields "disabled" rather than "s status is ".
_QUOTE_PATTERNS = [
    re.compile(r"[\"“”]([^\"“”]{1,60})[\"“”]"),
    re.compile(r"(?<![A-Za-z0-9])[\'‘’]([^\'‘’]{1,60})[\'‘’]"),
]


def _quoted(text: str) -> str | None:
    for pattern in _QUOTE_PATTERNS:
        match = pattern.search(text)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None

_LOGIN = re.compile(r"(?i)^\s*(?:log ?in|log ?on|sign ?in|authenticate)\b")
_NAVIGATE = re.compile(
    r"(?i)^\s*(?:navigate to|go to|open|visit|browse to|access)\s+(?:the\s+)?(.+?)"
    r"(?:\s+(?:page|screen|tab|section|view))?\s*$"
)
_CLICK = re.compile(
    r"(?i)^\s*(?:click(?:\s+on)?|press|tap|select|choose)\s+(?:the\s+)?(.+?)"
    r"(?:\s+(?:button|link|option|menu item|tab))?\s*$"
)
_FILL = re.compile(
    r"(?i)^\s*(?:enter|type|input|fill(?:\s+in)?|set)\s+(.+?)\s+(?:in|into|for)\s+"
    r"(?:the\s+)?(.+?)(?:\s+(?:field|box|input))?\s*$"
)
_API = re.compile(
    r"(?i)^\s*(?:call|send|make|issue|perform|do)?\s*(?:an?\s+)?"
    r"(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(?:request\s+)?(?:to\s+)?(/\S+)"
)
_VERIFY = re.compile(
    r"(?i)^\s*(?:verify|check|confirm|ensure|assert|observe|the user (?:should )?sees?|"
    r"should (?:see|display|show)|expect)\b\s*(?:that\s+)?(.*)$"
)
_URL_PATH = re.compile(r"(?:^|\s)(/[A-Za-z0-9._~\-/]*)")

_CREDENTIAL_WORD = re.compile(r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)\b")


def _slug_path(text: str) -> str:
    """Turn 'the User Management page' into '/user-management'."""
    inline = _URL_PATH.search(text)
    if inline:
        return inline.group(1)
    cleaned = re.sub(r"(?i)\b(the|a|an|page|screen|tab|section|view)\b", " ", text)
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").lower()
    return "/" + cleaned


def _label(text: str) -> str:
    quoted = _quoted(text)
    if quoted:
        return quoted
    return re.sub(r"(?i)\b(the|a|an)\b", " ", text).strip(" .\t")


def _assertion_for(text: str) -> dict[str, Any] | None:
    body = _label(text)
    if not body:
        return None
    path = _URL_PATH.search(text)
    if path and re.search(r"(?i)\b(url|redirect|navigat)", text):
        return {"action": "assert", "kind": "url_contains", "expected": path.group(1)}
    status = re.search(r"(?i)\b(?:http\s*)?(\d{3})\b", text)
    if status and re.search(r"(?i)\b(status|response|http|error code)\b", text):
        return {"action": "assert", "kind": "status", "expected": int(status.group(1))}
    quoted = _quoted(text)
    if quoted:
        return {"action": "assert", "kind": "text", "expected": quoted}
    # Unquoted prose makes a brittle text assertion; say so rather than emit it.
    return None


class Draft:
    """A draft plan plus an honest account of what could not be derived."""

    def __init__(self, case: ManualTestCase, analysis: Analysis) -> None:
        self.case = case
        self.analysis = analysis
        self.steps: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.todos: list[str] = []
        # Source step numbers that produced at least one DSL step. Coverage is
        # measured against these, not against the TODO count — a step can be
        # mapped and still carry a TODO ("confirm this guessed path").
        self.mapped: set[int] = set()

    def dedupe(self) -> None:
        """A case often states the same expectation per-step and again at the
        end. Keep the first; a draft that asserts twice just reads as noise."""
        seen: set[str] = set()
        kept: list[dict[str, Any]] = []
        for step in self.steps:
            if step.get("action") == "assert":
                key = repr(sorted(step.items(), key=lambda kv: kv[0]))
                if key in seen:
                    self.notes.append(
                        f"dropped a duplicate {step['kind']} assertion for "
                        f"{step.get('expected')!r}"
                    )
                    continue
                seen.add(key)
            kept.append(step)
        self.steps = kept

    def to_dict(self, environment: str, risk: str) -> dict[str, Any]:
        plan = {
            "version": 1,
            "name": self.case.title[:120],
            "description": f"Drafted from {self.case.id} ({self.case.source})",
            "environment": environment,
            "risk": risk,
            "tags": sorted({*self.case.tags, "drafted"}),
            "steps": self.steps,
        }
        valid, errors = True, []
        try:
            TestPlan.model_validate(plan)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            valid = False
            errors = [line.strip() for line in str(exc).splitlines() if line.strip()][:12]

        coverage = (
            round(100 * len(self.mapped) / len(self.case.steps)) if self.case.steps else 0
        )
        return sanitizer.scrub(
            {
                "case_id": self.case.id,
                "case_title": self.case.title,
                "plain_english": to_plain_english(plan, self.todos),
                "draft_plan": plan,
                "draft_is_valid": valid,
                "schema_errors": errors,
                "step_coverage_percent": coverage,
                "notes": self.notes,
                "todos": self.todos,
                "findings": [f.model_dump() for f in self.analysis.findings],
                "blockers": [f.message for f in self.analysis.blockers],
                "analysis": self.analysis.summary(),
                "review_required": True,
                "guidance": (
                    "This is a scaffold, not a finished test. Show the human "
                    "`plain_english` — they can read and edit that; they cannot read the "
                    "DSL. Resolve every TODO, confirm the guessed navigation paths, and "
                    "make sure each check actually proves the expected result before "
                    "saving or running it."
                ),
            }
        )


def _authenticate_step(draft: "Draft", analysis: Analysis) -> dict[str, Any]:
    if analysis.suggested_capability:
        return {"action": "authenticate", "capability": analysis.suggested_capability}
    draft.todos.append(
        "the case does not say who runs it — replace identity 'REPLACE_ME' with a real "
        "alias, or set a capability (see list_identities)"
    )
    return {"action": "authenticate", "identity": "REPLACE_ME"}


def draft_plan(
    case: ManualTestCase,
    environment: str,
    broker: IdentityBroker | None = None,
) -> dict[str, Any]:
    analysis = analyze(case, broker, environment)
    draft = Draft(case, analysis)

    blob = case.all_text()
    needs_login = bool(re.search(r"(?i)\b(log ?in|sign ?in|logged in|authenticated)\b", blob))
    added_auth = False

    if needs_login and not any(_LOGIN.match(s.action) for s in case.steps):
        # Preconditions often say "user is logged in" without a step for it.
        draft.steps.append(_authenticate_step(draft, analysis))
        added_auth = True
        draft.notes.append(
            "added an authenticate step from the preconditions"
            + (
                f" using capability {analysis.suggested_capability!r}"
                if analysis.suggested_capability
                else " — set the identity, the case does not say who"
            )
        )

    for step in case.steps:
        mapped = _map_step(step, analysis, draft, added_auth)
        if mapped:
            draft.mapped.add(step.number)
            added_auth = added_auth or mapped[0].get("action") == "authenticate"
            draft.steps.extend(mapped)

    # Expected results stated at case level, not per step.
    for expected in case.expected_results:
        assertion = _assertion_for(expected)
        if assertion:
            draft.steps.append(assertion)
            draft.notes.append(f"expected result → {assertion['kind']} assertion")
        else:
            draft.todos.append(
                f"expected result {expected!r} needs an explicit assertion "
                f"(a data-testid to check, an exact string, a URL, or a status code)"
            )

    if not any(s.get("action") == "assert" for s in draft.steps):
        draft.todos.append(
            "the draft has no assertions — it would pass without verifying anything"
        )

    draft.dedupe()

    security = [f for f in analysis.findings if f.kind == "security"]
    if security:
        return sanitizer.scrub(
            {
                "case_id": case.id,
                "case_title": case.title,
                "draft_plan": None,
                "refused": True,
                "reason": (
                    "This test case contains what looks like a literal credential. "
                    "Drafting was refused so the value does not get copied into a plan, "
                    "a file, or the conversation."
                ),
                "findings": [f.model_dump() for f in analysis.findings],
                "analysis": analysis.summary(),
                "remediation": [
                    "Remove the literal value from the source test case.",
                    "Store it in the secret store as a secret:// reference.",
                    "Add or update an identity in config/identities.yaml that points at it.",
                    "Rewrite the step as 'log in as <role>' and re-ingest.",
                ],
            }
        )

    return draft.to_dict(environment, analysis.inferred_risk)


def _map_step(
    step: ManualStep, analysis: Analysis, draft: Draft, added_auth: bool
) -> list[dict[str, Any]]:
    action = step.action.strip()
    out: list[dict[str, Any]] = []

    def with_expected(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if step.expected:
            assertion = _assertion_for(step.expected)
            if assertion:
                steps.append(assertion)
                draft.notes.append(f"step {step.number}: expectation → {assertion['kind']} assertion")
            else:
                draft.todos.append(
                    f"step {step.number}: expectation {step.expected!r} needs an explicit assertion"
                )
        return steps

    if _LOGIN.match(action):
        if added_auth:
            draft.notes.append(f"step {step.number}: already covered by the authenticate step")
            draft.mapped.add(step.number)
            return with_expected([])
        draft.notes.append(f"step {step.number}: login → authenticate (no credentials in the plan)")
        return with_expected([_authenticate_step(draft, analysis)])

    match = _NAVIGATE.match(action)
    if match:
        path = _slug_path(match.group(1))
        draft.notes.append(f"step {step.number}: navigate → {path}")
        draft.todos.append(
            f"step {step.number}: confirm the page really is at {path!r} — I worked that out "
            f"from your wording"
        )
        return with_expected([{"action": "navigate", "path": path}])

    match = _API.match(action)
    if match:
        method, path = match.group(1).upper(), match.group(2).rstrip(".,;")
        draft.notes.append(f"step {step.number}: {method} {path} → api_request")
        draft.todos.append(
            f"step {step.number}: set the identity for the {method} {path} call, or remove it "
            f"if the endpoint is meant to be reached unauthenticated"
        )
        step_dict: dict[str, Any] = {"action": "api_request", "method": method, "path": path}
        if analysis.suggested_identity:
            step_dict["identity"] = analysis.suggested_identity
            draft.todos[-1] = (
                f"step {step.number}: confirm {analysis.suggested_identity!r} is the right "
                f"identity for the {method} {path} call"
            )
        return with_expected([step_dict])

    match = _FILL.match(action)
    if match:
        value, field = match.group(1).strip(), _label(match.group(2))
        if _CREDENTIAL_WORD.search(field) or _CREDENTIAL_WORD.search(value):
            draft.todos.append(
                f"step {step.number}: fills a credential field. Do not put the value in the "
                f"plan — either let `authenticate` drive the login form, or use "
                f"`fill_secret` with a secret:// reference"
            )
            return with_expected([])
        draft.notes.append(f"step {step.number}: fill {field!r}")
        draft.todos.append(
            f"step {step.number}: check that {field!r} is the exact wording on the field"
        )
        return with_expected(
            [{"action": "fill", "target": {"label": field}, "value": _label(value)}]
        )

    match = _CLICK.match(action)
    if match:
        label = _label(match.group(1))
        draft.notes.append(f"step {step.number}: click {label!r}")
        draft.todos.append(
            f"step {step.number}: if {label!r} appears more than once on the page, say which "
            f'one — e.g. "Click {label} for <the row it is in>"'
        )
        return with_expected([{"action": "click", "target": {"text": label}}])

    match = _VERIFY.match(action)
    if match:
        assertion = _assertion_for(match.group(1) or action)
        if assertion:
            draft.notes.append(f"step {step.number}: verification → {assertion['kind']} assertion")
            return [assertion]
        draft.todos.append(
            f"step {step.number}: {action!r} is a verification, but the expected value is prose. "
            f"Give it an exact string, a data-testid, a URL fragment, or a status code"
        )
        return []

    draft.todos.append(f"step {step.number}: {action!r} could not be mapped to a DSL action")
    return out
