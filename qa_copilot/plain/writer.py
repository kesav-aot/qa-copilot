"""Render a Test DSL plan back into plain English.

The invariant that makes this worth having: what comes out here must compile
back to the same plan. That is what lets the whole pipeline hand a QA engineer
something they can read, edit and re-run — including plans a model wrote and
plans drafted from an imported test case.
"""

from __future__ import annotations

import re
from typing import Any


def _quote(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if not text.startswith(("/", "http")) else text


def _target_phrase(target: dict[str, Any] | None) -> tuple[str, str]:
    """Return (phrase, row-suffix)."""
    if not target:
        return "it", ""
    suffix = f' in the "{target["within"]}" row' if target.get("within") else ""
    if target.get("describe"):
        return str(target["describe"]), suffix
    # Precise targets round-trip because the resolver tries a test-id slug first.
    for key in ("testid", "name", "label", "text"):
        if target.get(key):
            return str(target[key]), suffix
    if target.get("css"):
        return str(target["css"]), suffix
    return "it", suffix


def step_to_english(step: dict[str, Any]) -> str | None:
    action = step.get("action")
    what, row = _target_phrase(step.get("target"))

    if action == "authenticate":
        if step.get("identity"):
            return f"Log in as {step['identity']}"
        capability = str(step.get("capability", "")).replace("_", " ")
        return f"Log in as someone who can {capability}"
    if action == "navigate":
        return f"Go to {step.get('path')}"
    if action == "click":
        return f"Click {what}{row}"
    if action == "fill":
        return f"Type {_quote(step.get('value'))} into {what}"
    if action == "fill_secret":
        return None  # no plain-English form; a secret reference must stay explicit
    if action == "select":
        return f"Select {_quote(step.get('option'))} from {what}"
    if action == "wait_for":
        if step.get("url_contains"):
            return f"Wait for the page to show {step['url_contains']}"
        return f"Wait for {what}"
    if action == "pause":
        return f"Wait {step.get('seconds')} seconds"
    if action == "screenshot":
        return f"Take a screenshot called {_quote(step.get('name', 'screenshot'))}"
    if action == "api_request":
        out = f"Call {step.get('method')} {step.get('path')}"
        if step.get("identity"):
            out += f" as {step['identity']}"
        if step.get("expect_status"):
            out += f", expecting {step['expect_status']}"
        return out
    if action == "assert":
        kind, expected = step.get("kind"), step.get("expected")
        if kind == "text":
            return f"Check the page shows {_quote(expected)}"
        if kind == "url_contains":
            return f"Check the URL contains {expected}"
        if kind == "status":
            return f"Check the status is {expected}"
        if kind == "visible":
            return f"Check I can see {what}{row}"
        if kind == "not_visible":
            return f"Check I should not see {what}{row}"
    return None


def to_plain_english(plan: dict[str, Any], todos: list[str] | None = None) -> str:
    """A complete, runnable plain-English file."""
    lines = [f"# {plan.get('name', 'Untitled test')}"]
    if plan.get("environment"):
        lines.append(f"Environment: {plan['environment']}")
    if plan.get("tags"):
        lines.append(f"Tags: {', '.join(plan['tags'])}")
    if plan.get("description"):
        lines.append(f"Description: {plan['description']}")
    lines.append("")

    unwritable: list[str] = []
    for index, step in enumerate(plan.get("steps", [])):
        english = step_to_english(step)
        if english is None:
            unwritable.append(f"step {index + 1} ({step.get('action')})")
            continue
        lines.append(english)

    if todos:
        lines.append("")
        lines.append("// Before you rely on this test, please check:")
        for todo in todos:
            lines.append(f"//   - {re.sub(r'\\s+', ' ', todo)}")
    if unwritable:
        lines.append("")
        lines.append(
            "// These steps have no plain-English form and were left out: "
            + ", ".join(unwritable)
        )
    return "\n".join(lines).rstrip() + "\n"
