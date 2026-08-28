"""qa-copilot CLI — the human half of the loop.

Approval lives here and only here. The MCP server exposes no tool that can
approve a plan, so a model cannot clear its own gate.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

import yaml

from qa_copilot.dsl.schema import json_schema
from qa_copilot.engine import QACopilot
from qa_copilot.plain import render_phrasebook
from qa_copilot.policy.engine import fingerprint
from qa_copilot.report import render_report, render_suite, render_understanding


def _load_plan(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _copilot(args) -> QACopilot:
    return QACopilot.load(Path(args.config), Path(args.state))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_environments(args) -> int:
    _print(_copilot(args).list_environments())
    return 0


def cmd_identities(args) -> int:
    _print(_copilot(args).list_identities(args.environment))
    return 0


def cmd_schema(args) -> int:
    _print(json_schema())
    return 0


def cmd_validate(args) -> int:
    result = _copilot(args).validate(_load_plan(Path(args.plan)))
    _print(result)
    return 0 if result.get("valid") and result.get("policy", {}).get("allowed") else 1


def _read(path: Path) -> str:
    if not path.is_file():
        print(f"There is no file called {path}", file=sys.stderr)
        raise SystemExit(2)
    return path.read_text(encoding="utf-8")


def cmd_run(args) -> int:
    """Run a test. Takes plain English or a DSL plan — it works out which."""
    copilot = _copilot(args)
    path = Path(args.plan)
    text = _read(path)

    if not copilot.is_plain_english(text):
        plan = _load_plan(path)
        report = asyncio.run(copilot.run(plan, headless=not args.headed))
        if args.json:
            _print(report)
        else:
            print(render_report(report, plan))
        return 0 if report.get("status") == "passed" else 1

    result = asyncio.run(
        copilot.run_plain(
            text,
            args.environment,
            headless=not args.headed,
            name=path.stem.replace("-", " ").replace("_", " "),
        )
    )
    if args.json:
        _print(result)
        return 0 if result.get("overall") == "passed" else 1

    if not result["understood"]:
        print("I could not understand parts of this test, so I did not run it.\n")
        print(render_understanding(result["compiled"]))
        print("Run `qa-copilot words` to see every phrase I know.")
        return 2

    for entry in result["results"]:
        print(render_report(entry["report"], entry["plan"]))
    if len(result["results"]) > 1:
        print(render_suite(
            {
                "plans_run": len(result["results"]),
                "counts": result["counts"],
                "results": [
                    {
                        "plan": e["name"],
                        "status": e["report"].get("status"),
                        "duration_ms": e["report"].get("duration_ms"),
                        "failure": (e["report"].get("failure") or {}).get("detail"),
                        "reason": e["report"].get("reason"),
                    }
                    for e in result["results"]
                ],
            }
        ))
    return 0 if result["overall"] == "passed" else 1


def cmd_check(args) -> int:
    """Show what QA Copilot understood, without running anything."""
    copilot = _copilot(args)
    path = Path(args.plan)
    text = _read(path)

    if not copilot.is_plain_english(text):
        result = copilot.validate(_load_plan(path))
        _print(result)
        return 0 if result.get("valid") and result.get("policy", {}).get("allowed") else 1

    compiled = copilot.compile_plain(
        text, args.environment, name=path.stem.replace("-", " ").replace("_", " ")
    )
    if args.json:
        _print(compiled)
    else:
        print(render_understanding(compiled))
        for test in compiled["tests"]:
            for note in test.get("notes", []):
                print(f"  ! {note}")
            for warning in test.get("warnings", []):
                print(f"  ! {warning}")
    return 0 if compiled["understood"] else 1


def cmd_init(args) -> int:
    """Point QA Copilot at an application, without writing any YAML."""
    from qa_copilot.setup.wizard import run_wizard

    return asyncio.run(
        run_wizard(
            config_dir=Path(args.config),
            url=args.url,
            environment=args.environment,
            login_path=args.login_path,
            headless=not args.headed,
        )
    )


def _desktop_config_path() -> Path:
    """Where Claude Desktop keeps its MCP settings, per platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        import os

        return Path(os.environ.get("APPDATA", "")) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _install_desktop(server: dict, force: bool) -> int:
    """Merge our entry into Claude Desktop's config, keeping everything else."""
    from datetime import datetime

    path = _desktop_config_path()
    if not path.parent.is_dir():
        print(
            f"Claude Desktop's settings folder is not there:\n  {path.parent}\n"
            f"Install Claude Desktop and open it once, then run this again.",
            file=sys.stderr,
        )
        return 2

    existing: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            print(
                f"{path} is not valid JSON ({exc}). Fix or move it, then try again.",
                file=sys.stderr,
            )
            return 2

    servers = existing.setdefault("mcpServers", {})
    if "qa-copilot" in servers and not force:
        print(
            "Claude Desktop already has a qa-copilot server configured.\n"
            "Re-run with --force to replace it.",
            file=sys.stderr,
        )
        return 1

    backup = None
    if path.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_suffix(f".json.backup-{stamp}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    servers["qa-copilot"] = server
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    print(f"Added qa-copilot to Claude Desktop.\n  {path}")
    if backup:
        print(f"  previous settings backed up to {backup.name}")
    print()
    print("Now quit Claude Desktop completely and reopen it — it only reads this")
    print("file at startup. Look for the tools icon in the message box.")
    return 0


def cmd_mcp_config(args) -> int:
    """Print the exact configuration to connect this to Claude Code, Claude
    Desktop or another agent, with absolute paths so it works from any
    directory."""
    root = Path.cwd().resolve()
    server = root / ".venv" / "bin" / "qa-copilot-mcp"
    if not server.is_file():
        print(
            f"{server} does not exist. Run this from the QA Copilot directory, "
            f"after `python3 -m venv .venv && .venv/bin/pip install -e .`",
            file=sys.stderr,
        )
        return 2

    config = {
        "command": str(server),
        "args": [],
        "env": {
            "QA_COPILOT_CONFIG": str(root / args.config),
            "QA_COPILOT_STATE": str(root / args.state),
        },
    }

    if args.json:
        _print({"mcpServers": {"qa-copilot": config}})
        return 0

    if args.desktop:
        if args.install:
            return _install_desktop(config, args.force)
        path = _desktop_config_path()
        print("Connect QA Copilot to Claude Desktop")
        print("=" * 36)
        print()
        print("Let me do it for you:")
        print()
        print("    qa-copilot mcp-config --desktop --install")
        print()
        print(f"Or add this to {path} by hand, under \"mcpServers\":")
        print()
        print(json.dumps({"mcpServers": {"qa-copilot": config}}, indent=2))
        print()
        print("Then quit Claude Desktop completely and reopen it.")
        return 0

    print("Connect QA Copilot to Claude Code")
    print("=" * 33)
    print()
    print("Option 1 — one command, works from any project:")
    print()
    print(f"    claude mcp add-json qa-copilot --scope user '{json.dumps(config)}'")
    print()
    print("Option 2 — paste this into your MCP settings by hand:")
    print()
    print(json.dumps({"mcpServers": {"qa-copilot": config}}, indent=2))
    print()
    print("Then run /mcp inside Claude Code. You should see qa-copilot with 17 tools.")
    print()
    print("Scopes: --scope user makes it available in every project (recommended);")
    print("        --scope project writes .mcp.json into the current repo, shared")
    print("        with anyone who clones it.")
    return 0


def cmd_words(args) -> int:
    """The phrasebook: everything QA Copilot understands."""
    print(render_phrasebook())
    return 0


def cmd_approve(args) -> int:
    copilot = _copilot(args)
    fp = args.fingerprint
    if args.plan:
        fp = fingerprint(copilot.parse(_load_plan(Path(args.plan))))
    if not fp:
        print("provide a fingerprint or --plan", file=sys.stderr)
        return 2
    approver = args.approver or getpass.getuser()
    _print(copilot.approve(fp, approver, args.note or ""))
    return 0


def cmd_revoke(args) -> int:
    ok = _copilot(args).revoke(args.fingerprint)
    print("revoked" if ok else "no such approval", file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


def cmd_audit(args) -> int:
    for entry in _copilot(args).audit.tail(args.limit):
        print(json.dumps(entry, default=str))
    return 0


# --- MVP 2: ingestion, library, suites ------------------------------------


def cmd_cases(args) -> int:
    result = _copilot(args).ingest_test_cases(args.source)
    if args.json:
        _print(result)
        return 0 if not result.get("errors") else 1
    for error in result.get("errors", []):
        print(f"  ! {error}", file=sys.stderr)
    header = f"{'ID':40} {'FORMAT':9} {'STEPS':>5} {'RISK':7} {'AUTO':5} TITLE"
    print(header)
    print("-" * len(header))
    for case in result.get("cases", []):
        analysis = case["analysis"]
        print(
            f"{case['id'][:40]:40} {case['format']:9} {case['steps']:>5} "
            f"{analysis['inferred_risk']:7} {'yes' if analysis['automatable'] else 'NO':5} "
            f"{case['title'][:40]}"
        )
        for blocker in case.get("blockers", []):
            print(f"{'':40} BLOCKER: {blocker}")
    print(f"\n{len(result.get('cases', []))} case(s) from {len(result.get('files_read', []))} file(s)")
    return 1 if result.get("errors") else 0


def cmd_analyze(args) -> int:
    result = _copilot(args).analyze_test_case(args.case_id, args.environment)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if args.json:
        _print(result)
        return 0
    case = result["case"]
    print(f"{case['id']}: {case['title']}")
    print(f"  source   {case['source']} ({case['format']})")
    print(f"  steps    {len(case['steps'])}, preconditions {len(case['preconditions'])}, "
          f"expected {len(case['expected_results'])}")
    summary = result["analysis"]
    print(f"  risk     {summary['inferred_risk']}  automatable: {summary['automatable']}")
    print(f"  actor    capability={summary['suggested_capability']} "
          f"identity={summary['suggested_identity']}")
    print("\nFindings:")
    for finding in result["findings"]:
        where = f" [{finding['location']}]" if finding.get("location") else ""
        fix = f"\n{'':12}→ {finding['suggestion']}" if finding.get("suggestion") else ""
        print(f"  {finding['severity'].upper():8} {finding['kind']:9}{where} {finding['message']}{fix}")
    return 0 if summary["automatable"] else 1


def cmd_draft(args) -> int:
    copilot = _copilot(args)
    result = copilot.draft_plan_from_test_case(args.case_id, args.environment)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if result.get("refused"):
        print(f"REFUSED: {result['reason']}\n", file=sys.stderr)
        for step in result.get("remediation", []):
            print(f"  - {step}", file=sys.stderr)
        return 2

    body = (
        yaml.safe_dump(result["draft_plan"], sort_keys=False, allow_unicode=True)
        if args.dsl
        else result["plain_english"]
    )
    if args.json:
        _print(result)
    else:
        print(body)
        print(f"// {result['step_coverage_percent']}% of the original steps were understood")

    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    if args.save:
        _print(copilot.save_plan(result["draft_plan"]))
    return 0


def cmd_plans(args) -> int:
    result = _copilot(args).list_plans()
    if args.json:
        _print(result)
        return 0
    header = f"{'SLUG':46} {'ENV':8} {'STEPS':>5} {'APPROVED':>8}  FINGERPRINT"
    print(header)
    print("-" * len(header))
    for plan in result["plans"]:
        if "error" in plan:
            print(f"{plan['slug'][:46]:46} !! {plan['error'][:60]}")
            continue
        print(
            f"{plan['slug'][:46]:46} {plan['environment'][:8]:8} {plan['steps']:>5} "
            f"{'yes' if plan['approved'] else 'no':>8}  {plan['fingerprint']}"
        )
    if result["suites"]:
        print("\nSuites:")
        for name, members in result["suites"].items():
            print(f"  {name}: {', '.join(members)}")
    return 0


def cmd_suite(args) -> int:
    copilot = _copilot(args)
    if args.list:
        _print(copilot.library.suites())
        return 0
    result = asyncio.run(
        copilot.run_suite(
            suite=args.name,
            plans=args.plan,
            headless=not args.headed,
            stop_on_failure=args.stop_on_failure,
        )
    )
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 2
    if args.json:
        _print(result)
    else:
        print(render_suite(result))
    return 0 if result["overall"] == "passed" else 1


# macOS refuses to let a sandboxed app (Claude Desktop) execute anything inside
# these folders. The failure looks like "Operation not permitted" in Claude's log
# and is very hard to guess at from there, so it is checked up front.
_TCC_PROTECTED = ("Documents", "Desktop", "Downloads", "Movies", "Music", "Pictures")


def _tcc_warning(root: Path) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        relative = root.resolve().relative_to(Path.home())
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in _TCC_PROTECTED:
        return None
    return (
        f"this project lives in ~/{relative.parts[0]}, which macOS protects. "
        f"Claude Code works, but Claude Desktop will fail to start the server "
        f"with 'Operation not permitted'. Move the project somewhere like "
        f"~/qa-copilot, or grant Claude Desktop Full Disk Access"
    )


def cmd_doctor(args) -> int:
    """Check that config, secrets and Playwright are actually wired up."""
    problems: list[str] = []
    warnings: list[str] = []
    copilot = _copilot(args)

    tcc = _tcc_warning(copilot.config.root)
    if tcc:
        warnings.append(tcc)
    if " " in str(copilot.config.root):
        warnings.append(
            "there is a space in this project's path. It works, but some agent "
            "hosts launch servers through a shell and quote badly — a path "
            "without spaces is one less thing to debug"
        )

    if not copilot.config.environments:
        problems.append("no environments configured")
    if not copilot.config.identities:
        problems.append("no identities configured")

    for identity in copilot.list_identities():
        if not identity["credentials_configured"]:
            problems.append(
                f"identity {identity['alias']} has no resolvable credentials "
                f"(provider: {copilot.provider.name})"
            )
    for env in copilot.config.environments.values():
        if env.login is None:
            problems.append(f"environment {env.name} has no login recipe")

    try:
        import playwright  # noqa: F401
    except ImportError:
        problems.append("playwright is not installed (pip install playwright)")
    else:
        chromium = list(Path.home().glob("Library/Caches/ms-playwright/chromium*")) or list(
            Path.home().glob(".cache/ms-playwright/chromium*")
        )
        if not chromium:
            problems.append("no chromium build found (run: playwright install chromium)")

    library = copilot.library
    if not copilot.config.plans_path.is_dir():
        problems.append(f"plan directory {copilot.config.plan_dir!r} does not exist")
    for entry in library.list():
        if "error" in entry:
            problems.append(f"plan {entry['slug']} does not parse: {entry['error']}")
    for suite_name in library.suites():
        try:
            library.resolve_suite(suite_name)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            problems.append(f"suite {suite_name!r}: {exc}")
    if not copilot.config.testcases_path.is_dir():
        problems.append(
            f"test-case directory {copilot.config.testcase_dir!r} does not exist "
            f"(only needed for `qa-copilot cases`)"
        )

    if problems:
        print("Problems found:")
        for p in problems:
            print(f"  - {p}")
        for w in warnings:
            print(f"  ! {w}")
        return 1
    print("All checks passed.")
    for w in warnings:
        print(f"  ! {w}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qa-copilot", description="Secret-blind QA automation")
    p.add_argument("--config", default="config", help="config directory")
    p.add_argument("--state", default=".qa-copilot", help="state directory")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the setup").set_defaults(func=cmd_doctor)
    sub.add_parser("environments", help="list environments").set_defaults(func=cmd_environments)
    sub.add_parser("schema", help="print the Test DSL JSON Schema").set_defaults(func=cmd_schema)

    ident = sub.add_parser("identities", help="list identities")
    ident.add_argument("--environment")
    ident.set_defaults(func=cmd_identities)

    val = sub.add_parser("validate", help="validate a plan and show the policy decision")
    val.add_argument("plan")
    val.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="run a test (plain English or a DSL plan)")
    run.add_argument("plan", metavar="FILE")
    run.add_argument("--environment", help="which environment to run against")
    run.add_argument("--headed", action="store_true", help="show the browser")
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser(
        "check", help="show what QA Copilot understood, without running anything"
    )
    check.add_argument("plan", metavar="FILE")
    check.add_argument("--environment")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    sub.add_parser(
        "words", help="every phrase you can write in a test"
    ).set_defaults(func=cmd_words)

    init = sub.add_parser(
        "init", help="point QA Copilot at an application (finds the login form for you)"
    )
    init.add_argument("--url", help="the app's web address")
    init.add_argument("--environment", help="what to call it, e.g. qa")
    init.add_argument("--login-path", help="path to the sign-in page, e.g. /signin")
    init.add_argument("--headed", action="store_true", help="watch the browser work")
    init.set_defaults(func=cmd_init)

    mcpc = sub.add_parser(
        "mcp-config", help="connect this to Claude Code or Claude Desktop"
    )
    mcpc.add_argument("--desktop", action="store_true", help="target Claude Desktop")
    mcpc.add_argument(
        "--install", action="store_true", help="write the settings (with --desktop)"
    )
    mcpc.add_argument("--force", action="store_true", help="replace an existing entry")
    mcpc.add_argument("--json", action="store_true")
    mcpc.set_defaults(func=cmd_mcp_config)

    app = sub.add_parser("approve", help="approve a plan for execution (humans only)")
    app.add_argument("fingerprint", nargs="?")
    app.add_argument("--plan", help="compute the fingerprint from a plan file")
    app.add_argument("--approver")
    app.add_argument("--note")
    app.set_defaults(func=cmd_approve)

    rev = sub.add_parser("revoke", help="revoke an approval")
    rev.add_argument("fingerprint")
    rev.set_defaults(func=cmd_revoke)

    cases = sub.add_parser("cases", help="ingest and analyse manual test cases")
    cases.add_argument("--source", help="file or subdirectory under the test-case directory")
    cases.add_argument("--json", action="store_true")
    cases.set_defaults(func=cmd_cases)

    ana = sub.add_parser("analyze", help="show one test case and every finding against it")
    ana.add_argument("case_id")
    ana.add_argument("--environment")
    ana.add_argument("--json", action="store_true")
    ana.set_defaults(func=cmd_analyze)

    draft = sub.add_parser("draft", help="scaffold a Test DSL plan from a manual test case")
    draft.add_argument("case_id")
    draft.add_argument("--environment", required=True)
    draft.add_argument("--out", help="write the draft to this file")
    draft.add_argument(
        "--dsl", action="store_true", help="emit the DSL plan instead of plain English"
    )
    draft.add_argument("--save", action="store_true", help="save straight into the plan library")
    draft.add_argument("--json", action="store_true")
    draft.set_defaults(func=cmd_draft)

    plans = sub.add_parser("plans", help="list the plan library and the defined suites")
    plans.add_argument("--json", action="store_true")
    plans.set_defaults(func=cmd_plans)

    suite = sub.add_parser("suite", help="run a named suite, or an explicit list of plans")
    suite.add_argument("name", nargs="?")
    suite.add_argument("--plan", action="append", help="plan slug; repeatable")
    suite.add_argument("--list", action="store_true", help="show the defined suites")
    suite.add_argument("--headed", action="store_true")
    suite.add_argument("--json", action="store_true")
    suite.add_argument("--stop-on-failure", action="store_true")
    suite.set_defaults(func=cmd_suite)

    aud = sub.add_parser("audit", help="tail the audit log")
    aud.add_argument("--limit", type=int, default=30)
    aud.set_defaults(func=cmd_audit)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
