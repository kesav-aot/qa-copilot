"""QACopilot — the trusted core that both the MCP server and the CLI drive.

Everything the model can reach goes through a method here, and every return
value leaves through :func:`qa_copilot.sanitize.sanitizer.scrub`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_copilot.audit.log import AuditLog
from qa_copilot.config import Config, load_config
from qa_copilot.dsl.schema import TestPlan
from qa_copilot.executor.api import ApiExecutor
from qa_copilot.executor.runner import PlanRunner
from qa_copilot.identity.broker import IdentityBroker
from qa_copilot.ingest import analyzer, drafter, loader
from qa_copilot.library import LibraryError, PlanLibrary
from qa_copilot.plain import Context, compile_text, looks_like_plain_english
from qa_copilot.policy.engine import ApprovalStore, PolicyEngine
from qa_copilot.sanitize import sanitizer
from qa_copilot.secrets import build_provider

STATE_DIR = ".qa-copilot"


class QACopilot:
    def __init__(
        self,
        config: Config,
        *,
        state_dir: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.config_dir = Path(config_dir).resolve() if config_dir else (config.root / "config")
        self.state = Path(state_dir) if state_dir else (self.config_dir.parent / STATE_DIR)
        self.state.mkdir(parents=True, exist_ok=True)

        sc = config.secrets
        kwargs: dict[str, Any] = {}
        if sc.provider == "env" and sc.dotenv:
            kwargs["dotenv"] = config.dotenv_path
        if sc.provider == "file":
            kwargs["path"] = config.secret_file_path or (config.root / "secrets.enc.yaml")
        self.provider = build_provider(sc.provider, **kwargs)

        self.broker = IdentityBroker(config, self.provider)
        self.api = ApiExecutor(config, self.broker)
        self.approvals = ApprovalStore(self.state / "approvals")
        self.policy = PolicyEngine(config.policy, self.approvals)
        self.audit = AuditLog(self.state / "audit.jsonl")
        self.runner = PlanRunner(config, self.broker, self.api, self.audit)
        self.library = PlanLibrary(
            config.plans_path, suites_file=self.config_dir / "suites.yaml"
        )
        self.testcase_root = config.testcases_path

    @classmethod
    def load(cls, config_dir: Path | None = None, state_dir: Path | None = None) -> "QACopilot":
        return cls(
            load_config(config_dir),
            state_dir=state_dir,
            config_dir=config_dir or Path("config"),
        )

    # --- discovery (safe to expose) ---------------------------------------
    def list_environments(self) -> list[dict[str, Any]]:
        return [e.public_view() for e in self.config.environments.values()]

    def list_identities(self, environment: str | None = None) -> list[dict[str, Any]]:
        return self.broker.list_identities(environment)

    def list_capabilities(self) -> list[str]:
        return self.broker.list_capabilities()

    # --- plan lifecycle ---------------------------------------------------
    def parse(self, plan: dict[str, Any] | TestPlan) -> TestPlan:
        return plan if isinstance(plan, TestPlan) else TestPlan.model_validate(plan)

    def validate(self, plan: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = self.parse(plan)
        except ValidationError as exc:
            return sanitizer.scrub(
                {
                    "valid": False,
                    "errors": [
                        {
                            "location": ".".join(str(p) for p in e["loc"]),
                            "message": e["msg"],
                        }
                        for e in exc.errors()
                    ],
                }
            )

        decision = self.policy.evaluate(parsed)
        if parsed.environment not in self.config.environments:
            decision.allowed = False
            decision.violations.append(
                f"unknown environment {parsed.environment!r}; configured: "
                + (", ".join(sorted(self.config.environments)) or "<none>")
            )
        missing = self._missing_identities(parsed)
        self.audit.write(
            "plan.validate",
            plan=parsed.name,
            risk=str(decision.risk),
            fingerprint=decision.fingerprint,
            allowed=decision.allowed,
        )
        return sanitizer.scrub(
            {
                "valid": True,
                "plan": parsed.name,
                "policy": decision.to_dict(),
                "unresolved_identities": missing,
                "next_step": self._next_step(decision, missing),
            }
        )

    def _missing_identities(self, plan: TestPlan) -> list[str]:
        missing: list[str] = []
        for step in plan.steps:
            alias = getattr(step, "identity", None)
            capability = getattr(step, "capability", None)
            try:
                if alias or capability:
                    self.broker.resolve(
                        identity=alias, capability=capability, environment=plan.environment
                    )
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller
                missing.append(str(exc))
        return sorted(set(missing))

    @staticmethod
    def _next_step(decision, missing: list[str]) -> str:
        if missing:
            return "resolve the unresolved identities, then re-validate"
        if not decision.allowed:
            return "policy violations must be fixed before this plan can run"
        if decision.requires_approval and not decision.approved:
            return (
                f"a human must approve this plan: "
                f"`qa-copilot approve {decision.fingerprint}`"
            )
        return "ready to run"

    async def run(self, plan: dict[str, Any], *, headless: bool = True) -> dict[str, Any]:
        try:
            parsed = self.parse(plan)
        except ValidationError as exc:
            return sanitizer.scrub(
                {"status": "invalid", "errors": [e["msg"] for e in exc.errors()]}
            )

        decision = self.policy.evaluate(parsed)
        if parsed.environment not in self.config.environments:
            decision.allowed = False
            decision.violations.append(
                f"unknown environment {parsed.environment!r}; configured: "
                + (", ".join(sorted(self.config.environments)) or "<none>")
            )
        if not decision.can_execute:
            self.audit.write(
                "plan.blocked",
                plan=parsed.name,
                fingerprint=decision.fingerprint,
                violations=decision.violations,
                requires_approval=decision.requires_approval,
            )
            return sanitizer.scrub(
                {
                    "status": "blocked",
                    "plan": parsed.name,
                    "policy": decision.to_dict(),
                    "reason": self._next_step(decision, []),
                }
            )

        try:
            report = await self.runner.run(parsed, headless=headless)
        except Exception as exc:  # noqa: BLE001 - a crash must not escape as a stack trace
            self.audit.write("plan.error", plan=parsed.name, detail=str(exc))
            return sanitizer.scrub(
                {
                    "status": "error",
                    "plan": parsed.name,
                    "policy": decision.to_dict(),
                    "failure": {"detail": f"{type(exc).__name__}: {exc}"},
                }
            )
        report["policy"] = decision.to_dict()
        return sanitizer.scrub(report)

    # --- approvals (CLI only — no MCP tool grants these) ------------------
    def approve(self, fingerprint: str, approver: str, note: str = "") -> dict[str, Any]:
        record = self.approvals.approve(fingerprint, approver=approver, note=note)
        self.audit.write("plan.approved", **record)
        return record

    def revoke(self, fingerprint: str) -> bool:
        revoked = self.approvals.revoke(fingerprint)
        if revoked:
            self.audit.write("plan.approval_revoked", fingerprint=fingerprint)
        return revoked

    # --- test-case ingestion (MVP 2) --------------------------------------
    def ingest_test_cases(self, target: str | None = None) -> dict[str, Any]:
        """Parse manual test cases and analyse each one for automation gaps."""
        try:
            result = loader.ingest(self.testcase_root, target)
        except loader.IngestError as exc:
            return sanitizer.scrub({"error": str(exc), "cases": [], "errors": [str(exc)]})

        cases = []
        for case in result.cases:
            analysis = analyzer.analyze(case, self.broker)
            cases.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "format": case.format,
                    "source": case.source,
                    "steps": len(case.steps),
                    "tags": case.tags,
                    "analysis": analysis.summary(),
                    "blockers": [f.message for f in analysis.blockers],
                }
            )
        self.audit.write(
            "testcases.ingested",
            target=str(target or self.testcase_root),
            found=len(cases),
            errors=len(result.errors),
        )
        return sanitizer.scrub(
            {
                "cases": cases,
                "files_read": result.files_read,
                "errors": result.errors,
                "supported_formats": sorted(set(loader.SUFFIXES.values())),
            }
        )

    def find_test_case(self, case_id: str, target: str | None = None):
        result = loader.ingest(self.testcase_root, target)
        wanted = case_id.strip().lower()
        for case in result.cases:
            if case.id.lower() == wanted:
                return case
        known = ", ".join(c.id for c in result.cases[:20]) or "<none found>"
        raise LibraryError(f"no test case with id {case_id!r}. Found: {known}")

    def analyze_test_case(self, case_id: str, environment: str | None = None) -> dict[str, Any]:
        try:
            case = self.find_test_case(case_id)
        except (LibraryError, loader.IngestError) as exc:
            return sanitizer.scrub({"error": str(exc)})
        analysis = analyzer.analyze(case, self.broker, environment)
        return sanitizer.scrub(
            {
                "case": case.model_dump(mode="json"),
                "analysis": analysis.summary(),
                "findings": [f.model_dump() for f in analysis.findings],
            }
        )

    def draft_plan_from_test_case(self, case_id: str, environment: str) -> dict[str, Any]:
        try:
            case = self.find_test_case(case_id)
        except (LibraryError, loader.IngestError) as exc:
            return sanitizer.scrub({"error": str(exc)})
        if environment not in self.config.environments:
            return sanitizer.scrub(
                {
                    "error": f"unknown environment {environment!r}; configured: "
                    + (", ".join(sorted(self.config.environments)) or "<none>")
                }
            )
        draft = drafter.draft_plan(case, environment, self.broker)
        self.audit.write(
            "plan.drafted",
            case_id=case.id,
            environment=environment,
            coverage=draft.get("step_coverage_percent"),
            todos=len(draft.get("todos", [])),
        )
        return draft

    # --- plan library ------------------------------------------------------
    def save_plan(self, plan: dict[str, Any], overwrite: bool = True) -> dict[str, Any]:
        try:
            parsed = self.parse(plan)
        except ValidationError as exc:
            return sanitizer.scrub(
                {"saved": False, "errors": [e["msg"] for e in exc.errors()]}
            )
        decision = self.policy.evaluate(parsed)
        try:
            info = self.library.save(parsed, overwrite=overwrite)
        except LibraryError as exc:
            return sanitizer.scrub({"saved": False, "errors": [str(exc)]})
        self.audit.write("plan.saved", slug=info["slug"], fingerprint=info["fingerprint"])
        return sanitizer.scrub({"saved": True, **info, "policy": decision.to_dict()})

    def list_plans(self) -> dict[str, Any]:
        entries = self.library.list()
        for entry in entries:
            fp = entry.get("fingerprint")
            entry["approved"] = bool(fp) and self.approvals.is_approved(fp)
        return sanitizer.scrub({"plans": entries, "suites": self.library.suites()})

    def get_plan(self, name: str) -> dict[str, Any]:
        try:
            plan = self.library.load(name)
        except LibraryError as exc:
            return sanitizer.scrub({"error": str(exc)})
        return sanitizer.scrub({"plan": plan.model_dump(mode="json", exclude_none=True)})

    # --- suites ------------------------------------------------------------
    async def run_suite(
        self,
        *,
        suite: str | None = None,
        plans: list[str] | None = None,
        headless: bool = True,
        stop_on_failure: bool = False,
    ) -> dict[str, Any]:
        try:
            names = self.library.resolve_suite(suite) if suite else list(plans or [])
        except LibraryError as exc:
            return sanitizer.scrub({"error": str(exc)})
        if not names:
            return sanitizer.scrub({"error": "give a suite name or a list of plan slugs"})

        results: list[dict[str, Any]] = []
        counts = {"passed": 0, "failed": 0, "error": 0, "blocked": 0, "invalid": 0}
        for name in names:
            try:
                plan = self.library.load(name)
            except LibraryError as exc:
                results.append({"plan": name, "status": "error", "detail": str(exc)})
                counts["error"] += 1
                if stop_on_failure:
                    break
                continue

            report = await self.run(plan.model_dump(mode="json"), headless=headless)
            status = report.get("status", "error")
            counts[status] = counts.get(status, 0) + 1
            results.append(
                {
                    "plan": name,
                    "status": status,
                    "duration_ms": report.get("duration_ms"),
                    "failure": (report.get("failure") or {}).get("detail"),
                    "reason": report.get("reason"),
                    "artifacts": report.get("artifacts", []),
                }
            )
            if stop_on_failure and status != "passed":
                break

        overall = "passed" if counts["passed"] == len(results) and results else "failed"
        self.audit.write("suite.finish", suite=suite, plans=len(results), overall=overall)
        return sanitizer.scrub(
            {
                "suite": suite,
                "overall": overall,
                "counts": counts,
                "plans_run": len(results),
                "plans_requested": len(names),
                "results": results,
            }
        )

    # --- plain English (the surface a QA engineer actually uses) -----------
    def compile_plain(
        self, text: str, environment: str | None = None, name: str = "Untitled test"
    ) -> dict[str, Any]:
        """Turn a plain-English test file into plans, with a line-by-line account
        of what each sentence was understood to mean."""
        ctx = Context.from_copilot(self, environment)
        result = compile_text(text, ctx, default_name=name)
        payload = result.to_dict()

        # Attach the policy verdict so the writer learns about the approval gate
        # while they are still editing, not when they press run.
        for compiled, out in zip(result.tests, payload["tests"], strict=False):
            if compiled.plan is None:
                continue
            try:
                decision = self.policy.evaluate(self.parse(compiled.plan))
            except Exception:  # noqa: BLE001 - validation already reported above
                continue
            out["policy"] = decision.to_dict()
            if decision.requires_approval and not decision.approved:
                out.setdefault("notes", []).append(
                    f"This test changes data, so someone has to approve it before it "
                    f"can run: qa-copilot approve {decision.fingerprint}"
                )
            out["warnings"] = compiled.warnings
        return sanitizer.scrub(payload)

    async def run_plain(
        self,
        text: str,
        environment: str | None = None,
        *,
        headless: bool = True,
        name: str = "Untitled test",
    ) -> dict[str, Any]:
        """Compile and run a plain-English file. Refuses to run anything if any
        test in the file failed to compile — a half-understood file is more
        dangerous than none."""
        compiled = self.compile_plain(text, environment, name)
        if not compiled["understood"]:
            return sanitizer.scrub(
                {"understood": False, "ran": False, "compiled": compiled, "results": []}
            )

        results = []
        counts = {"passed": 0, "failed": 0, "error": 0, "blocked": 0, "invalid": 0}
        for test in compiled["tests"]:
            report = await self.run(test["plan"], headless=headless)
            status = report.get("status", "error")
            counts[status] = counts.get(status, 0) + 1
            results.append({"name": test["name"], "plan": test["plan"], "report": report})

        overall = "passed" if counts["passed"] == len(results) and results else "failed"
        return sanitizer.scrub(
            {
                "understood": True,
                "ran": True,
                "overall": overall,
                "counts": counts,
                "compiled": compiled,
                "results": results,
            }
        )

    @staticmethod
    def is_plain_english(text: str) -> bool:
        return looks_like_plain_english(text)
