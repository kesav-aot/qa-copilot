"""Render results the way a QA engineer reads them.

JSON is for the machine; this is for the person. Every line answers one of three
questions: what did it do, what went wrong, and what should I do next.
"""

from __future__ import annotations

import shutil
from typing import Any

TICK = "✓"
CROSS = "✗"
DOT = "·"
PAUSE = "⏸"


def _width() -> int:
    return min(shutil.get_terminal_size((88, 24)).columns, 96)


# --- turning a DSL step back into English ----------------------------------

def describe_step(step: dict[str, Any]) -> str:
    """Say what a plan step does, without DSL vocabulary."""
    action = step.get("action")
    target = step.get("target") or {}

    def where() -> str:
        if target.get("describe"):
            out = str(target["describe"])
            if target.get("within"):
                out += f' in the "{target["within"]}" row'
            return out
        for key in ("testid", "name", "label", "text", "role", "css"):
            if target.get(key):
                return f'{target[key]}'
        return "it"

    if action == "authenticate":
        if step.get("identity"):
            return f"log in as {step['identity']}"
        return f"log in as someone who can {str(step.get('capability', '')).replace('_', ' ')}"
    if action == "navigate":
        return f"go to {step.get('path')}"
    if action == "click":
        return f"click {where()}"
    if action == "fill":
        return f"type \"{step.get('value')}\" into {where()}"
    if action == "fill_secret":
        return f"fill {where()} from the secret store"
    if action == "select":
        return f"choose \"{step.get('option')}\" from {where()}"
    if action == "wait_for":
        if step.get("url_contains"):
            return f"wait for the address to contain {step['url_contains']}"
        return f"wait for {where()}"
    if action == "pause":
        return f"wait {step.get('seconds')} seconds"
    if action == "screenshot":
        return f"take a screenshot ({step.get('name')})"
    if action == "api_request":
        out = f"call {step.get('method')} {step.get('path')}"
        if step.get("identity"):
            out += f" as {step['identity']}"
        if step.get("expect_status"):
            out += f", expecting {step['expect_status']}"
        return out
    if action == "assert":
        kind, expected = step.get("kind"), step.get("expected")
        if kind == "text":
            return f'check the page shows "{expected}"'
        if kind == "url_contains":
            return f'check the address contains "{expected}"'
        if kind == "status":
            return f"check the last API call returned {expected}"
        if kind == "visible":
            return f"check {where()} is on the page"
        if kind == "not_visible":
            return f"check {where()} is NOT on the page"
    return str(action)


# --- rendering a run --------------------------------------------------------

_HEADLINE = {
    "passed": "PASSED",
    "failed": "FAILED",
    "error": "COULD NOT RUN",
    "blocked": "NEEDS APPROVAL",
    "invalid": "NOT A VALID TEST",
}


def render_report(report: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    """One test's result, as prose plus a checklist."""
    name = report.get("plan") or report.get("name") or "Test"
    status = report.get("status", "error")
    headline = _HEADLINE.get(status, status.upper())
    duration = report.get("duration_ms")
    timing = f"  ({duration / 1000:.1f}s)" if isinstance(duration, (int, float)) else ""

    width = _width()
    left = str(name)[: width - len(headline) - len(timing) - 4]
    lines = [
        "",
        f"{left}{' ' * max(1, width - len(left) - len(headline) - len(timing))}{headline}{timing}",
        "─" * width,
    ]

    if status == "blocked":
        policy = report.get("policy") or {}
        lines += [
            "",
            "  This test was not run because it needs a person to approve it first.",
            f"  Reason: it is {policy.get('risk', 'medium')} risk"
            + (" (it changes or removes data)" if policy.get("risk") != "low" else ""),
            "",
            "  Ask someone on the team to run:",
            f"      qa-copilot approve {policy.get('fingerprint', '<fingerprint>')}",
        ]
        for violation in policy.get("violations") or []:
            lines.append(f"  ! {violation}")
        return "\n".join(lines) + "\n"

    if status == "invalid":
        lines.append("")
        for err in report.get("errors", []):
            lines.append(f"  ! {err}")
        return "\n".join(lines) + "\n"

    plan_steps = (plan or {}).get("steps") or []
    results = report.get("steps") or []
    for entry in results:
        index = entry.get("index")
        source = plan_steps[index] if isinstance(index, int) and index < len(plan_steps) else {}
        text = describe_step(source) if source else entry.get("action", "")
        mark = TICK if entry.get("status") == "passed" else CROSS
        took = entry.get("duration_ms")
        suffix = f"   {took}ms" if isinstance(took, int) and took > 400 else ""
        lines.append(f"  {mark}  {text}{suffix}")
        if entry.get("status") != "passed":
            detail = str(entry.get("detail", "")).rstrip()
            lines.append("")
            for detail_line in detail.splitlines():
                lines.append(f"     {detail_line}")
            lines.append("")

    ran, total = report.get("steps_run", len(results)), report.get("steps_total", len(results))
    if status != "passed" and total > ran:
        skipped = total - ran
        lines.append(
            f"  {DOT}  stopped here — {skipped} later step{'s' if skipped != 1 else ''} did not run"
        )

    failure = report.get("failure") or {}
    if failure.get("screenshot"):
        lines += ["", f"  Screenshot of the failure: {failure['screenshot']}"]
    elif report.get("artifacts"):
        lines += ["", "  Screenshots:"]
        lines += [f"    {a}" for a in report["artifacts"]]

    if status == "error" and not results:
        lines += ["", f"  {failure.get('detail', 'something went wrong before the test started')}"]

    return "\n".join(lines) + "\n"


def render_understanding(compiled: dict[str, Any]) -> str:
    """What the compiler made of a plain-English file."""
    out: list[str] = []
    for test in compiled.get("tests", []):
        out.append("")
        out.append(f"{test['name']}")
        out.append("─" * min(len(test["name"]), _width()))
        for step in test.get("steps", []):
            out.append(f"  line {step['line']:>3}  {step['you_wrote']}")
            out.append(f"           {DOT} {step['i_understood']}")
            if step.get("warning"):
                out.append(f"           ! {step['warning']}")
        problems = test.get("problems", [])
        if problems:
            out.append("")
            out.append(f"  {len(problems)} line{'s' if len(problems) != 1 else ''} I could not use:")
            for problem in problems:
                where = f"line {problem['line']}: " if problem["line"] else ""
                out.append(f"    {CROSS} {where}{problem['you_wrote']}")
                for detail in str(problem["problem"]).splitlines():
                    out.append(f"        {detail}")
                if problem.get("suggestion"):
                    for i, suggestion in enumerate(str(problem["suggestion"]).splitlines()):
                        prefix = "try: " if i == 0 else "     "
                        out.append(f"        {prefix}{suggestion}")
        out.append("")
        out.append(
            f"  {TICK} ready to run" if test.get("understood")
            else f"  {CROSS} fix the lines above, then try again"
        )
    return "\n".join(out) + "\n"


def render_suite(result: dict[str, Any]) -> str:
    """Several tests at once — the summary a QA lead reads."""
    counts = result.get("counts", {})
    total = result.get("plans_run", 0)
    width = _width()
    out = ["", f"{total} test{'s' if total != 1 else ''} run", "─" * width]
    for entry in result.get("results", []):
        status = entry.get("status", "error")
        mark = {"passed": TICK, "blocked": PAUSE}.get(status, CROSS)
        took = entry.get("duration_ms")
        timing = f"{took / 1000:.1f}s" if isinstance(took, (int, float)) else ""
        name = str(entry.get("plan", ""))[: width - 22]
        out.append(f"  {mark}  {name:<{max(10, width - 22)}} {status:<8} {timing:>6}")
        reason = entry.get("failure") or entry.get("reason")
        if reason and status != "passed":
            out.append(f"        {str(reason).splitlines()[0][: width - 10]}")
    out.append("─" * width)
    summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    out.append(f"  {summary or 'nothing ran'}")
    if counts.get("blocked"):
        out.append("  Blocked tests need a person to approve them: qa-copilot plans")
    return "\n".join(out) + "\n"
