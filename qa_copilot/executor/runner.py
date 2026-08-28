"""Plan runner — walks a validated, policy-cleared TestPlan.

Returns a report that is sanitised at the boundary. A step failure stops the run
(fail-fast) and the report carries a screenshot of the failure state.
"""

from __future__ import annotations

import asyncio
import re
import time
import traceback
from pathlib import Path
from typing import Any

from qa_copilot.audit.log import AuditLog
from qa_copilot.config import Config
from qa_copilot.dsl.schema import TestPlan
from qa_copilot.executor.api import ApiExecutor
from qa_copilot.executor.browser import BrowserSession, ExecutionError, open_session
from qa_copilot.executor.resolver import ResolutionError
from qa_copilot.identity.broker import IdentityBroker
from qa_copilot.sanitize import sanitizer

def safe_dirname(name: str) -> str:
    """Plan names come from a model; they must not steer where files are written."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return (cleaned or "plan")[:60]


_UI_ACTIONS = {
    "authenticate", "navigate", "click", "double_click", "fill", "fill_secret",
    "select", "wait_for", "screenshot",
}


class PlanRunner:
    def __init__(
        self,
        config: Config,
        broker: IdentityBroker,
        api: ApiExecutor,
        audit: AuditLog,
    ) -> None:
        self.config = config
        self.broker = broker
        self.api = api
        self.audit = audit

    def _needs_browser(self, plan: TestPlan) -> bool:
        if any(s.action in _UI_ACTIONS for s in plan.steps):
            return True
        return any(
            s.action == "assert" and s.kind in {"visible", "not_visible", "text", "url_contains"}
            for s in plan.steps
        )

    async def run(self, plan: TestPlan, *, headless: bool = True) -> dict[str, Any]:
        env = self.config.environment(plan.environment)
        artifact_dir = self.config.artifacts_path / safe_dirname(plan.name)
        session: BrowserSession | None = None
        results: list[dict[str, Any]] = []
        artifacts: list[str] = []
        status = "passed"
        failure: dict[str, Any] | None = None
        self._last_api_status: int | None = None
        started = time.perf_counter()

        self.audit.write(
            "plan.start", plan=plan.name, environment=plan.environment, steps=len(plan.steps)
        )

        try:
            if self._needs_browser(plan):
                try:
                    session = await open_session(env, artifact_dir, headless=headless)
                except Exception as exc:  # noqa: BLE001 - reported as a run error
                    self.audit.write("plan.error", plan=plan.name, detail=str(exc))
                    return sanitizer.scrub(
                        {
                            "plan": plan.name,
                            "environment": plan.environment,
                            "status": "error",
                            "steps_run": 0,
                            "steps_total": len(plan.steps),
                            "steps": [],
                            "artifacts": [],
                            "failure": {
                                "step_index": None,
                                "action": "open_browser",
                                "detail": (
                                    f"could not start the browser: {type(exc).__name__}: {exc}. "
                                    "Run `qa-copilot doctor`."
                                ),
                            },
                            "secret_values_exposed": False,
                        }
                    )

            for index, step in enumerate(plan.steps):
                t0 = time.perf_counter()
                entry: dict[str, Any] = {"index": index, "action": step.action, "status": "passed"}
                try:
                    detail, api_status = await self._run_step(
                        step, session, plan, artifacts, artifact_dir
                    )
                    if api_status is not None:
                        self._last_api_status = api_status
                    entry["detail"] = detail
                except AssertionError as exc:
                    entry.update(status="failed", detail=str(exc))
                    status = "failed"
                except ResolutionError as exc:
                    # Already written for a person to read. Do not decorate it.
                    entry.update(status="failed", detail=str(exc), kind="element_not_resolved")
                    status = "failed"
                except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                    entry.update(
                        status="error",
                        detail=f"{type(exc).__name__}: {exc}",
                        trace=traceback.format_exc(limit=3),
                    )
                    status = "error"
                entry["duration_ms"] = round((time.perf_counter() - t0) * 1000)
                results.append(entry)

                if entry["status"] != "passed":
                    failure = {
                        "step_index": index,
                        "action": step.action,
                        "detail": entry["detail"],
                        "step": step.model_dump(mode="json", exclude_none=True),
                    }
                    if session is not None:
                        try:
                            shot = await session.screenshot(f"failure-step{index}")
                            artifacts.append(shot)
                            failure["screenshot"] = shot
                            failure["page"] = await session.snapshot(max_chars=1_500)
                            # A step that "worked" but changed nothing is often a
                            # JavaScript error the page swallowed.
                            console = session.console_errors()
                            if console:
                                failure["console_errors"] = console
                        except Exception:
                            pass
                    break
        finally:
            if session is not None:
                await session.close()

        report = {
            "plan": plan.name,
            "environment": plan.environment,
            "risk": str(plan.risk),
            "status": status,
            "steps_run": len(results),
            "steps_total": len(plan.steps),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "steps": results,
            "artifacts": artifacts,
            "failure": failure,
            "secret_values_exposed": False,
        }
        self.audit.write("plan.finish", plan=plan.name, status=status, steps_run=len(results))
        return sanitizer.scrub(report)

    async def _run_step(
        self,
        step: Any,
        session: BrowserSession | None,
        plan: TestPlan,
        artifacts: list[str],
        artifact_dir: Path,
    ) -> tuple[str, int | None]:
        action = step.action

        if action in _UI_ACTIONS and session is None:
            raise ExecutionError(f"step {action!r} requires a browser session")

        if action == "authenticate":
            identity = self.broker.resolve(
                identity=step.identity, capability=step.capability, environment=plan.environment
            )
            env = self.config.environment(plan.environment)
            if env.login is None:
                raise ExecutionError(
                    f"environment {plan.environment!r} has no login recipe configured"
                )
            creds = self.broker.credentials(identity)
            self.audit.write(
                "auth.attempt", identity=identity.alias, environment=plan.environment
            )
            result = await session.login(env.login, creds)  # type: ignore[union-attr]
            self.audit.write(
                "auth.result", identity=identity.alias, authenticated=result["authenticated"]
            )
            if not result["authenticated"]:
                raise AssertionError(
                    f"authentication as {identity.alias} failed: {result['detail']}"
                )
            return f"authenticated as {identity.alias} ({result['detail']})", None

        if action == "navigate":
            url = await session.navigate(step.path)  # type: ignore[union-attr]
            return f"at {url}", None

        if action == "click":
            popup_url = await session.click(step.target)  # type: ignore[union-attr]
            if popup_url:
                # Say so loudly: every later step now runs against the new window.
                return (
                    f"clicked {step.target.summary()} — it opened a window, "
                    f"now working in {popup_url}"
                ), None
            return f"clicked {step.target.summary()}", None

        if action == "double_click":
            popup_url = await session.double_click(step.target)  # type: ignore[union-attr]
            if popup_url:
                return (
                    f"double-clicked {step.target.summary()} — it opened a window, "
                    f"now working in {popup_url}"
                ), None
            return f"double-clicked {step.target.summary()}", None

        if action == "fill":
            await session.fill(step.target, step.value)  # type: ignore[union-attr]
            return f"filled {step.target.summary()}", None

        if action == "fill_secret":
            secret = self.broker.resolve_secret(step.secret_ref)
            await session.fill_secret(step.target, secret)  # type: ignore[union-attr]
            self.audit.write("secret.injected", ref=step.secret_ref, target=step.target.summary())
            return f"filled {step.target.summary()} from {step.secret_ref} (value not exposed)", None

        if action == "select":
            await session.select(step.target, step.option)  # type: ignore[union-attr]
            return f"selected {step.option!r}", None

        if action == "wait_for":
            await session.wait_for(  # type: ignore[union-attr]
                step.target, step.url_contains, step.timeout_ms
            )
            return "wait satisfied", None

        if action == "pause":
            await asyncio.sleep(step.seconds)
            return f"waited {step.seconds}s", None

        if action == "screenshot":
            path = await session.screenshot(step.name)  # type: ignore[union-attr]
            artifacts.append(path)
            return f"captured {path}", None

        if action == "api_request":
            identity_alias = step.identity
            if step.capability and not identity_alias:
                identity_alias = self.broker.resolve(
                    identity=None, capability=step.capability, environment=plan.environment
                ).alias
            resp = await self.api.request(
                environment=plan.environment,
                method=step.method,
                path=step.path,
                identity=identity_alias,
                json_body=step.json_body,
            )
            self.audit.write(
                "api.request",
                identity=identity_alias,
                method=step.method,
                path=step.path,
                status=resp["status"],
            )
            if step.expect_status is not None and resp["status"] != step.expect_status:
                raise AssertionError(
                    f"{step.method} {step.path} returned {resp['status']}, "
                    f"expected {step.expect_status}"
                )
            return f"{step.method} {step.path} -> {resp['status']}", resp["status"]

        if action == "assert":
            return await self._run_assert(step, session), None

        raise ExecutionError(f"unsupported action {action!r}")

    async def _run_assert(self, step: Any, session: BrowserSession | None) -> str:
        kind = step.kind
        if kind in {"visible", "not_visible"}:
            if session is None:
                raise ExecutionError("visibility assertions require a browser session")
            state, detail = await session.visibility(step.target)

            if kind == "visible":
                if state == "visible":
                    return f"{step.target.summary()} is visible"
                # Carry the resolver's explanation through — it names what *is*
                # on the page, which is what the reader needs next.
                raise AssertionError(
                    f"I expected to see {step.target.summary()}.\n{detail}"
                )

            if state == "visible":
                raise AssertionError(
                    f"{step.target.summary()} should not be on the page, but it is"
                )
            return (
                f"{step.target.summary()} is absent"
                if state == "missing"
                else f"{step.target.summary()} is present but hidden"
            )

        if kind == "text":
            if session is None:
                raise ExecutionError("text assertions require a browser session")
            if not await session.text_present(str(step.expected)):
                raise AssertionError(f"text {step.expected!r} not found on {session.url()}")
            return f"text {step.expected!r} present"

        if kind == "url_contains":
            if session is None:
                raise ExecutionError("url assertions require a browser session")
            if str(step.expected) not in session.url():
                raise AssertionError(
                    f"URL {session.url()!r} does not contain {step.expected!r}"
                )
            return f"url contains {step.expected!r}"

        if kind == "status":
            actual = getattr(self, "_last_api_status", None)
            if actual is None:
                raise AssertionError("no api_request has run yet, nothing to assert status on")
            if actual != int(step.expected):
                raise AssertionError(f"last API status was {actual}, expected {step.expected}")
            return f"last API status is {step.expected}"

        raise ExecutionError(f"unsupported assertion kind {kind!r}")
