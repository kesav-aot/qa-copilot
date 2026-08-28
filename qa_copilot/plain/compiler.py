"""Compile a plain-English test file into Test DSL plans.

The output is not just a plan. It is a plan *plus a line-by-line account of what
each sentence was understood to mean*, because the person writing the test needs
to check the tool understood them — and they cannot read the DSL.

Nothing is ever silently ignored. A line that does not parse is an error with a
suggestion, not a skipped line.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from qa_copilot.dsl.schema import TestPlan
from qa_copilot.plain.grammar import RULES, Built

_COMMENT = re.compile(r"^\s*(?://|--|;)")
_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_HEADER = re.compile(
    r"(?i)^\s*(?P<key>test|name|scenario|environment|env|tags?|risk|description|about)"
    r"\s*:\s*(?P<value>.*)$"
)
_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(?P<body>.+)$")

# Checked before any rule, so a credential is always answered with the alias
# workflow rather than falling through to "I do not understand this line".
_CREDENTIAL_IN_LINE = [
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|passphrase)\b\s*(?:is|=|:)?\s+[\"']?"
        r"((?=\S*\d)(?=\S*[A-Za-z])\S{6,})"
    ),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\b\s*(?:is|=|:)?\s+[\"']?(\S{12,})"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),
    re.compile(r"\b(?:sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _credential_problem(line: "Line", ctx: "Context") -> "Problem | None":
    if not any(pattern.search(line.text) for pattern in _CREDENTIAL_IN_LINE):
        return None
    return Problem(
        line=line.number,
        text=re.sub(r"(?i)(password|token|key|secret)(\s*(?:is|=|:)?\s+)\S+",
                    r"\1\2********", line.text),
        message=(
            "this line looks like it contains a real password or key.\n"
            "Credentials must never be written in a test file — anyone who can read "
            "the file can read the password, and it would end up in the AI's context."
        ),
        suggestion=(
            f'say who instead: "Log in as an admin"\n'
            f"available test accounts: {ctx.identity_list()}\n"
            f"if the account you need is missing, ask whoever set this up to add it "
            f"to config/identities.yaml and the secret store"
        ),
    )
_TEST_ID = re.compile(r"^\s*(?:[A-Z][A-Z0-9]*-\d+|TC[-_]?\d+)\s*[:\-–]\s*")


@dataclass
class Line:
    number: int
    text: str


@dataclass
class Problem:
    line: int
    text: str
    message: str
    suggestion: str | None = None

    def render(self) -> str:
        out = f"  line {self.line}: {self.text}\n      {self.message}"
        if self.suggestion:
            out += f"\n      try: {self.suggestion}"
        return out


@dataclass
class StepReading:
    """One source line and what it was understood to mean."""

    line: int
    text: str
    explain: str
    warning: str | None = None


@dataclass
class CompiledTest:
    name: str
    plan: dict[str, Any] | None
    readings: list[StepReading] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan is not None and not self.problems


@dataclass
class CompileResult:
    tests: list[CompiledTest] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tests) and all(t.ok for t in self.tests)

    @property
    def problems(self) -> list[Problem]:
        return [p for t in self.tests for p in t.problems]

    def to_dict(self) -> dict[str, Any]:
        return {
            "understood": self.ok,
            "tests": [
                {
                    "name": t.name,
                    "plan": t.plan,
                    "understood": t.ok,
                    "steps": [
                        {
                            "line": r.line,
                            "you_wrote": r.text,
                            "i_understood": r.explain,
                            "warning": r.warning,
                        }
                        for r in t.readings
                    ],
                    "problems": [
                        {
                            "line": p.line,
                            "you_wrote": p.text,
                            "problem": p.message,
                            "suggestion": p.suggestion,
                        }
                        for p in t.problems
                    ],
                }
                for t in self.tests
            ],
        }


class Context:
    """What the compiler needs to know about this workspace."""

    def __init__(self, identities: list[str], capabilities: list[str], environments: list[str],
                 default_environment: str | None = None, broker=None) -> None:
        self.identities = identities
        self.capabilities = set(capabilities)
        self.environments = environments
        self.default_environment = default_environment or (environments[0] if environments else "")
        self._broker = broker

    @classmethod
    def from_copilot(cls, copilot, environment: str | None = None) -> "Context":
        environments = sorted(copilot.config.environments)
        return cls(
            identities=sorted(copilot.config.identities),
            capabilities=copilot.broker.list_capabilities(),
            environments=environments,
            default_environment=environment
            or (copilot.config.policy.allowed_environments or environments or [""])[0],
            broker=copilot.broker,
        )

    def identity_list(self) -> str:
        return ", ".join(self.identities) or "<none configured>"

    def capability_list(self) -> str:
        return ", ".join(sorted(c.replace("_", " ") for c in self.capabilities)) or "<none>"

    def identity_for_capability(self, capability: str) -> str | None:
        if self._broker is None:
            return None
        try:
            return self._broker.select(capability, self.default_environment).alias
        except Exception:
            return None


# --- splitting the file into tests -----------------------------------------

def _split_tests(text: str) -> list[tuple[str | None, dict[str, str], list[Line]]]:
    """Return [(name, headers, step lines)]. A heading or a `Test:` line starts
    a new test; content before the first one belongs to an unnamed test."""
    blocks: list[tuple[str | None, dict[str, str], list[Line]]] = []
    name: str | None = None
    headers: dict[str, str] = {}
    lines: list[Line] = []

    def flush() -> None:
        if lines or name:
            blocks.append((name, dict(headers), list(lines)))

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or _COMMENT.match(raw):
            continue

        heading = _HEADING.match(raw)
        header = _HEADER.match(raw)

        if heading or (header and header.group("key").lower() in {"test", "name", "scenario"}):
            flush()
            title = heading.group("title") if heading else header.group("value")  # type: ignore[union-attr]
            name = _TEST_ID.sub("", title).strip() or title.strip()
            headers, lines = {}, []
            continue

        if header and not lines:
            # Headers only count before the first step, so a step that happens to
            # contain a colon is not mistaken for one.
            headers[header.group("key").lower()] = header.group("value").strip()
            continue

        numbered = _NUMBERED.match(raw)
        lines.append(Line(number, (numbered.group("body") if numbered else stripped).strip()))

    flush()
    return blocks


# --- compiling one line -----------------------------------------------------

def _suggest(text: str) -> str | None:
    """Nearest example from the phrasebook, so the error teaches the fix."""
    examples = [ex for rule in RULES for ex in rule.examples]
    close = difflib.get_close_matches(text, examples, n=1, cutoff=0.6)
    if close:
        return close[0]
    first = text.split()[0].lower() if text.split() else ""
    verbs = {
        "click": 'Click the Save button',
        "go": "Go to /users",
        "open": "Open the Dashboard page",
        "type": 'Type "hello" into the Search box',
        "enter": "Enter 2 in the Quantity field",
        "check": 'Check the page shows "Done"',
        "verify": 'Verify "Access denied" is displayed',
        "login": "Log in as an admin",
        "log": "Log in as an admin",
        "wait": 'Wait for the page to show "Done"',
        "select": 'Select "Premium" from the Plan dropdown',
    }
    return verbs.get(first)


def compile_line(text: str, ctx: Context) -> Built | None:
    for rule in RULES:
        match = rule.pattern.match(text)
        if match:
            return rule.build(match, ctx)
    return None


def compile_text(text: str, ctx: Context, default_name: str = "Untitled test") -> CompileResult:
    result = CompileResult()

    for name, headers, lines in _split_tests(text):
        test_name = name or headers.get("test") or headers.get("name") or default_name
        compiled = CompiledTest(name=test_name, plan=None)

        environment = headers.get("environment") or headers.get("env") or ctx.default_environment
        if ctx.environments and environment not in ctx.environments:
            compiled.problems.append(
                Problem(
                    line=0,
                    text=f"Environment: {environment}",
                    message=f'there is no environment called "{environment}"',
                    suggestion=f"one of: {', '.join(ctx.environments)}",
                )
            )

        steps: list[dict[str, Any]] = []
        for line in lines:
            credential = _credential_problem(line, ctx)
            if credential:
                compiled.problems.append(credential)
                continue
            built = compile_line(line.text, ctx)
            if built is None:
                compiled.problems.append(
                    Problem(
                        line=line.number,
                        text=line.text,
                        message="I do not understand this line",
                        suggestion=_suggest(line.text)
                        or "run `qa-copilot words` to see every phrase I know",
                    )
                )
                continue
            if built.error:
                compiled.problems.append(
                    Problem(
                        line=line.number,
                        text=line.text,
                        message=built.error,
                        suggestion=built.suggestion,
                    )
                )
                continue
            steps.extend(built.steps)
            compiled.readings.append(
                StepReading(line.number, line.text, built.explain, built.warning)
            )
            if built.warning:
                compiled.warnings.append(f"line {line.number}: {built.warning}")

        if not steps and not compiled.problems:
            compiled.problems.append(
                Problem(0, test_name, "this test has no steps", "add at least one line")
            )

        if not any(s.get("action") == "assert" for s in steps):
            compiled.warnings.append(
                "this test never checks anything, so it can only fail if the app "
                'crashes. Add a line like: Check the page shows "..."'
            )

        if steps and not compiled.problems:
            candidate: dict[str, Any] = {
                "version": 1,
                "name": test_name[:120],
                "environment": environment,
                "steps": steps,
            }
            if headers.get("description") or headers.get("about"):
                candidate["description"] = headers.get("description") or headers.get("about")
            if headers.get("tags") or headers.get("tag"):
                raw_tags = headers.get("tags") or headers.get("tag") or ""
                candidate["tags"] = [t for t in re.split(r"[,\s]+", raw_tags) if t]
            if headers.get("risk"):
                candidate["risk"] = headers["risk"].strip().lower()

            try:
                TestPlan.model_validate(candidate)
                compiled.plan = candidate
            except ValidationError as exc:
                for err in exc.errors()[:5]:
                    compiled.problems.append(
                        Problem(
                            0,
                            test_name,
                            f"the finished test is not valid: {err['msg']}",
                            "this is probably a bug in QA Copilot — please report it",
                        )
                    )

        result.tests.append(compiled)

    if not result.tests:
        result.tests.append(
            CompiledTest(
                name=default_name,
                plan=None,
                problems=[Problem(0, "", "the file is empty", "write one step per line")],
            )
        )
    return result


def looks_like_plain_english(text: str) -> bool:
    """Distinguish a plain-English file from a YAML plan, so `run` can take
    either without the user having to say which."""
    head = text.lstrip()
    if head.startswith(("version:", "- ", "{")):
        return False
    if re.search(r"(?m)^\s*steps\s*:\s*$", text) and re.search(r"(?m)^\s*-\s+action\s*:", text):
        return False
    return True
