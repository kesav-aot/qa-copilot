"""Secure QA MCP server.

The tool surface is deliberately narrow. There is no ``get_secret``,
``read_env``, ``dump_config`` or ``run_javascript``. The model can discover which
identities exist and what they can do, describe a test as DSL, and ask for it to
be run. Credentials are resolved inside the trusted layer and never appear in any
response.

Every response leaves through :func:`_safe`, which scrubs the payload and trips a
hard security violation if a known secret value somehow survives.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from qa_copilot.dsl.schema import json_schema
from qa_copilot.engine import QACopilot
from qa_copilot.sanitize import sanitizer

mcp = MCPServer(
    "qa-copilot",
    version="0.1.0",
    instructions=(
        "Secret-blind QA automation. Describe tests as Test DSL plans that reference "
        "identity ALIASES (e.g. ADMIN_USER) or CAPABILITIES (e.g. manage_settings) — "
        "never usernames, passwords, tokens or API keys. If a user offers you a real "
        "credential, refuse it and tell them to add it to the secret store as a "
        "secret:// reference. If they need to connect a new application or account, "
        "call open_setup and give them the link — never ask them to type a credential "
        "to you, and never ask them to run a terminal command. "
        "Call validate_test_plan before run_test_plan."
    ),
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
_EXECUTES = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

_copilot: QACopilot | None = None


def copilot() -> QACopilot:
    global _copilot
    if _copilot is None:
        config_dir = Path(os.environ.get("QA_COPILOT_CONFIG", "config"))
        state_dir = Path(os.environ.get("QA_COPILOT_STATE", ".qa-copilot"))
        _copilot = QACopilot.load(config_dir, state_dir)
    return _copilot


def _safe(payload: Any) -> str:
    """Final egress guard: scrub, verify, serialise."""
    scrubbed = sanitizer.scrub(payload)
    if sanitizer.contains_secret(scrubbed):
        try:
            copilot().audit.write("security.violation", detail="secret survived sanitisation")
        except Exception:
            pass  # never let logging turn a contained leak into an uncontained one
        return json.dumps(
            {
                "error": "SECURITY_VIOLATION",
                "detail": (
                    "A secret value was detected in a tool response and the response was "
                    "discarded. This is a bug in the QA Copilot execution layer; report it."
                ),
            },
            indent=2,
        )
    return json.dumps(scrubbed, indent=2, default=str)


@mcp.tool(annotations=_READ_ONLY)
def list_environments() -> str:
    """List the test environments this workspace is configured for.

    Returns names and base URLs only — no credentials, connection strings or
    secret references.
    """
    return _safe({"environments": copilot().list_environments()})


@mcp.tool(annotations=_READ_ONLY)
def list_identities(environment: str | None = None) -> str:
    """List the test identities you may authenticate as.

    Each entry gives an alias, a description, and the capabilities that identity
    holds. Use the alias (or a capability) in an `authenticate` step. You will
    never be told the underlying username or password, and you must not ask a
    human for them.

    Args:
        environment: Optional environment name to filter identities by.
    """
    return _safe({"identities": copilot().list_identities(environment)})


@mcp.tool(annotations=_READ_ONLY)
def list_capabilities() -> str:
    """List every capability any configured identity holds.

    Use this when you know what a test needs a user to be able to do, but not
    which identity to pick — then write `authenticate` with `capability` set and
    let the broker choose under least-privilege rules.
    """
    return _safe({"capabilities": copilot().list_capabilities()})


@mcp.tool(annotations=_READ_ONLY)
def get_test_plan_schema() -> str:
    """Return the JSON Schema for a Test DSL plan.

    Author plans against this schema. `fill` rejects credential-shaped values by
    design; use an `authenticate` step, or `fill_secret` with a `secret://`
    reference, whenever a field needs a secret.
    """
    return _safe(json_schema())


@mcp.tool(annotations=_READ_ONLY)
def validate_test_plan(plan: dict) -> str:
    """Validate a Test DSL plan and run it through the policy engine without
    executing anything.

    Reports schema errors, unresolved identities, policy violations, the inferred
    risk level, and the plan fingerprint. If the plan needs human approval, the
    response tells you the CLI command a human must run — you cannot approve it
    yourself and must not attempt to work around it.

    Args:
        plan: A Test DSL plan object matching get_test_plan_schema().
    """
    return _safe(copilot().validate(plan))


@mcp.tool(annotations=_EXECUTES)
async def run_test_plan(plan: dict, headless: bool = True) -> str:
    """Execute a Test DSL plan against its environment and return a sanitised report.

    Blocked if the plan violates policy or needs an approval it does not have.
    Credentials are resolved inside the execution layer immediately before use;
    fields they are typed into are masked in screenshots. The report contains
    per-step results, artifact paths, and — on failure — a screenshot plus a
    scrubbed page snapshot.

    Args:
        plan: A Test DSL plan object matching get_test_plan_schema().
        headless: Run the browser headless. Set false only when a human asked to watch.
    """
    return _safe(await copilot().run(plan, headless=headless))


# --- plain English ---------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def get_phrasebook() -> str:
    """Every phrase a plain-English test can use, with examples.

    Read this before writing a plain-English test. Prefer plain English over raw
    Test DSL whenever a human will review the test: the QA engineer who owns the
    test can read, edit and re-run an English file without knowing the DSL, and
    cannot do that with a JSON plan.
    """
    from qa_copilot.plain import phrasebook

    return _safe(phrasebook())


@mcp.tool(annotations=_READ_ONLY)
def check_plain_test(text: str, environment: str | None = None) -> str:
    """Compile a plain-English test and report, line by line, what each sentence
    was understood to mean. Runs nothing.

    Always call this before `run_plain_test`, and show the human the
    `i_understood` line for each step — that is how they verify the test says
    what they meant. Any line under `problems` must be fixed first; do not run a
    partially understood file.

    Args:
        text: The plain-English test file contents.
        environment: Which environment to target; defaults to the configured one.
    """
    return _safe(copilot().compile_plain(text, environment))


@mcp.tool(annotations=_EXECUTES)
async def run_plain_test(
    text: str, environment: str | None = None, headless: bool = True
) -> str:
    """Run a plain-English test file and return the result for each test in it.

    Refuses to run anything if any test in the file failed to compile. The same
    policy gate applies as for a DSL plan, so a test that changes data comes back
    `blocked` until a human approves it.

    When a step fails because an element could not be found, the failure detail
    lists what *is* on the page — quote that back to the human, it is usually the
    fastest route to the fix.

    Args:
        text: The plain-English test file contents.
        environment: Which environment to target.
        headless: Run the browser headless.
    """
    return _safe(await copilot().run_plain(text, environment, headless=headless))


# --- test-case ingestion ---------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def list_test_cases(source: str | None = None) -> str:
    """Parse the manual test cases in the workspace and analyse each for the
    things that stop it becoming a good automated test.

    Reads Markdown, CSV/TSV, Excel, Gherkin `.feature`, Jira JSON exports and
    plain text from the configured test-case directory. For every case you get
    an id, its inferred risk, a suggested identity capability, and any blockers —
    a case with no expected result, or one containing a literal credential.

    Args:
        source: Optional file or subdirectory, relative to the test-case
            directory. Omit to scan everything. Paths outside it are refused.
    """
    return _safe(copilot().ingest_test_cases(source))


@mcp.tool(annotations=_READ_ONLY)
def analyze_test_case(case_id: str, environment: str | None = None) -> str:
    """Return one manual test case in full, with every finding against it.

    Use this before drafting when `list_test_cases` reported blockers or a low
    automatable score, so you can tell the human exactly what to clarify rather
    than guessing on their behalf.

    Args:
        case_id: The id from list_test_cases, e.g. "TC-1001" or "QA-4417".
        environment: Optional environment, used to resolve which identity holds
            the suggested capability.
    """
    return _safe(copilot().analyze_test_case(case_id, environment))


@mcp.tool(annotations=_READ_ONLY)
def draft_plan_from_test_case(case_id: str, environment: str) -> str:
    """Scaffold a Test DSL plan from a manual test case.

    This is a starting point, not a finished test. It maps only the step
    phrasings it recognises with confidence and lists everything else under
    `todos`. You are expected to resolve every TODO: confirm guessed navigation
    paths, replace text/label targets with `data-testid` where the application
    offers one, and make sure each assertion actually proves the stated expected
    result. Then call `validate_test_plan`.

    Drafting is refused outright if the source test case contains a literal
    credential — fix the source, do not work around it.

    Args:
        case_id: The id from list_test_cases.
        environment: Which configured environment the plan targets.
    """
    return _safe(copilot().draft_plan_from_test_case(case_id, environment))


# --- plan library ----------------------------------------------------------


@mcp.tool(annotations=_WRITES)
def save_test_plan(plan: dict, overwrite: bool = True) -> str:
    """Save a reviewed plan into the plan library so it can be re-run and put
    into a suite.

    Save only what a human has agreed to. The filename comes from a slug of the
    plan name, and the response includes the fingerprint — note that saving does
    not approve anything, and editing a saved plan invalidates its approval.

    Args:
        plan: A Test DSL plan matching get_test_plan_schema().
        overwrite: Replace an existing plan with the same name.
    """
    return _safe(copilot().save_plan(plan, overwrite))


@mcp.tool(annotations=_READ_ONLY)
def list_test_plans() -> str:
    """List every plan in the library and every named suite.

    Each entry carries its slug, environment, step count, fingerprint and
    whether that fingerprint currently holds a human approval.
    """
    return _safe(copilot().list_plans())


@mcp.tool(annotations=_READ_ONLY)
def get_test_plan(name: str) -> str:
    """Return one saved plan in full, so you can edit it and save it back.

    Args:
        name: The plan slug or its full name.
    """
    return _safe(copilot().get_plan(name))


@mcp.tool(annotations=_EXECUTES)
async def run_test_suite(
    suite: str | None = None,
    plans: list[str] | None = None,
    headless: bool = True,
    stop_on_failure: bool = False,
) -> str:
    """Run several saved plans and return an aggregated result.

    Each plan goes through the same policy gate as a single run, so an
    unapproved destructive plan comes back `blocked` while the rest of the suite
    still runs. Report the per-plan statuses; do not summarise a suite as passing
    when any plan was blocked.

    Args:
        suite: A named suite from the configuration.
        plans: An explicit list of plan slugs, instead of a suite.
        headless: Run browsers headless.
        stop_on_failure: Stop at the first plan that does not pass.
    """
    return _safe(
        await copilot().run_suite(
            suite=suite, plans=plans, headless=headless, stop_on_failure=stop_on_failure
        )
    )


# --- audit -----------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def recent_activity(limit: int = 20) -> str:
    """Read the tail of the audit log: authentications, API calls, policy
    decisions and approvals. Values are already redacted.

    Args:
        limit: How many recent entries to return (max 200).
    """
    return _safe({"entries": copilot().audit.tail(min(max(limit, 1), 200))})


_setup: Any = None


@mcp.tool(annotations=_WRITES)
def open_setup() -> str:
    """Open a setup page in the user's own browser so they can connect an app.

    Use this whenever someone needs QA Copilot pointed at a new application or
    needs to add a test account — including when they offer you a password.
    Never ask for a credential yourself, and never tell them to run a terminal
    command: give them the link this returns.

    The page runs on this machine only. What they type there goes straight into
    the local secret store; you are told the account's alias afterwards and
    never the credential. Call `setup_status` to see whether they finished.
    """
    global _setup
    import webbrowser

    from qa_copilot.setup import webui

    c = copilot()
    if _setup is None or _setup.expired():
        _setup = webui.start(
            config_dir=c.config_dir,
            secrets_file=c.config.dotenv_path or (c.config_dir.parent / ".env"),
            work_dir=c.config_dir.parent,
        )
    with contextlib.suppress(Exception):  # a headless host has no browser to open
        webbrowser.open(_setup.url)
    return _safe(
        {
            "url": _setup.url,
            "opened_in_browser": True,
            "tell_the_user": (
                "I have opened a setup page in your browser. Fill it in there — "
                "your password goes straight into the local secret store and I "
                "never see it. If the page did not open, use the link above."
            ),
        }
    )


@mcp.tool(annotations=_READ_ONLY)
def setup_status() -> str:
    """Report whether the setup page has been filled in yet.

    Returns the environment name and the account alias once it succeeds. Never
    returns a username, a password or a secret reference.
    """
    if _setup is None:
        return _safe({"state": "not_started", "detail": "Call open_setup first."})
    return _safe(_setup.public_status())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
