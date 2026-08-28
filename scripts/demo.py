#!/usr/bin/env python3
"""End-to-end proof: drive the QA Copilot MCP server exactly as an AI client would.

Starts the demo target app, speaks MCP over stdio to the server, and walks the
whole loop — discover identities, author a plan, validate, run, hit the approval
gate, get approval, run again. Finally it greps every byte the "model" received
for the demo credentials, which is the claim the project has to make good on.

    .venv/bin/python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402

from qa_copilot.policy.engine import fingerprint  # noqa: E402
from qa_copilot.dsl.schema import TestPlan  # noqa: E402

DEMO_SECRETS = ["Adm1n-Demo-Pass!", "Us3r-Demo-Pass!", "admin@qa.local", "user@qa.local"]
TRANSCRIPT: list[str] = []

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def heading(text: str) -> None:
    print(f"\n{BOLD}── {text} {'─' * max(0, 66 - len(text))}{RESET}")


def show(label: str, payload: str, limit: int = 900) -> None:
    TRANSCRIPT.append(payload)
    body = payload if len(payload) <= limit else payload[:limit] + f"\n… ({len(payload)} chars)"
    print(f"{DIM}{label}{RESET}\n{body}")


async def call(session: ClientSession, tool: str, **args) -> dict:
    result = await session.call_tool(tool, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    show(f"← {tool}", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


async def main() -> int:
    env = {
        **os.environ,
        "QA_SECRET__DEMO__ADMIN__USERNAME": "admin@qa.local",
        "QA_SECRET__DEMO__ADMIN__PASSWORD": "Adm1n-Demo-Pass!",
        "QA_SECRET__DEMO__USER__USERNAME": "user@qa.local",
        "QA_SECRET__DEMO__USER__PASSWORD": "Us3r-Demo-Pass!",
        "PYTHONPATH": str(ROOT),
        "FLASK_APP": "demo_app.app",
    }

    heading("starting the demo target app on :8099")
    app = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--port", "8099", "--host", "127.0.0.1"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)
    print("demo app up. Its credentials live only in .env — the model below never sees them.")

    shutil.rmtree(ROOT / ".qa-copilot" / "approvals", ignore_errors=True)

    params = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "qa-copilot-mcp"), args=[], env=env
    )

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                heading("1. what the model can see")
                tools = await session.list_tools()
                print("tools exposed:", ", ".join(t.name for t in tools.tools))
                await call(session, "list_environments")
                await call(session, "list_identities", environment="demo")

                heading("2. a read-only plan, validated then run")
                plan = yaml.safe_load(
                    (ROOT / "examples" / "admin-can-reach-user-management.yaml").read_text()
                )
                await call(session, "validate_test_plan", plan=plan)
                report = await call(session, "run_test_plan", plan=plan)
                print(f"\nverdict: {GREEN if report.get('status') == 'passed' else RED}"
                      f"{report.get('status')}{RESET}")

                heading("3. a destructive plan hits the approval gate")
                risky = yaml.safe_load((ROOT / "examples" / "disable-user.yaml").read_text())
                blocked = await call(session, "run_test_plan", plan=risky)
                print(f"\nverdict: {blocked.get('status')} — {blocked.get('reason')}")

                heading("4. a human approves out of band, then it runs")
                fp = fingerprint(TestPlan.model_validate(risky))
                subprocess.run(
                    [str(ROOT / ".venv" / "bin" / "qa-copilot"), "approve", fp,
                     "--approver", "qa-lead", "--note", "reviewed in demo"],
                    cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL,
                )
                print(f"$ qa-copilot approve {fp}")
                approved = await call(session, "run_test_plan", plan=risky)
                print(f"\nverdict: {GREEN if approved.get('status') == 'passed' else RED}"
                      f"{approved.get('status')}{RESET}")

                heading("5. a manual test case becomes an automated one")
                cases = await call(session, "list_test_cases", source="user-management.md")
                for case in cases.get("cases", []):
                    marker = "" if case["analysis"]["automatable"] else "  <- not automatable"
                    print(f"  {case['id']:10} {case['title'][:44]:44}{marker}")

                drafted = await call(
                    session, "draft_plan_from_test_case", case_id="TC-1001", environment="demo"
                )
                print(f"\ncoverage {drafted['step_coverage_percent']}% of steps mapped; "
                      f"{len(drafted['todos'])} TODO(s) left for a human")
                await call(session, "save_test_plan", plan=drafted["draft_plan"])

                heading("6. the analyser refuses a test case containing a credential")
                bad = await call(
                    session, "draft_plan_from_test_case", case_id="TC-9001", environment="demo"
                )
                print(f"\nrefused: {bad.get('refused')} — {bad.get('reason', '')[:90]}")

                heading("7. running a suite")
                suite = await call(session, "run_test_suite", suite="authz")
                print(f"\noverall: {GREEN if suite.get('overall') == 'passed' else RED}"
                      f"{suite.get('overall')}{RESET}  {suite.get('counts')}")

                heading("8. the model asks for a secret directly")
                names = {t.name for t in tools.tools}
                for forbidden in ("get_secret", "read_secret", "read_env", "approve_plan"):
                    verdict = f"{GREEN}not exposed{RESET}" if forbidden not in names else f"{RED}EXPOSED{RESET}"
                    print(f"  {forbidden:14} {verdict}")
                print("  There is no tool to call. The boundary is the absence of an API,")
                print("  not a rule the model is asked to respect.")

                heading("9. audit trail")
                await call(session, "recent_activity", limit=8)
    finally:
        app.terminate()
        app.wait(timeout=10)

    heading("the actual claim")
    everything = "\n".join(TRANSCRIPT)
    leaked = [s for s in DEMO_SECRETS if s in everything]
    print(f"bytes the model received: {len(everything)}")
    if leaked:
        print(f"{RED}FAIL — these credentials appeared in model-visible output: {leaked}{RESET}")
        return 1
    print(f"{GREEN}PASS — none of the four demo credentials appear anywhere in "
          f"model-visible output, across a real browser login.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
