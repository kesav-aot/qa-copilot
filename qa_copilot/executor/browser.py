"""Playwright browser session — the only place in the system that types a secret.

This module is on the trusted side of the boundary (``SecretValue.reveal()`` is
permitted here). Two obligations come with that:

* every element a secret is typed into is registered as a screenshot mask;
* nothing returned from this module is handed to the model without passing
  through :mod:`qa_copilot.sanitize.sanitizer` first.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qa_copilot.config import Environment, LoginRecipe
from qa_copilot.dsl.schema import Target
from qa_copilot.executor.resolver import ResolutionError, resolve
from qa_copilot.identity.broker import Credentials
from qa_copilot.sanitize import sanitizer
from qa_copilot.secrets.base import SecretValue

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Browser, Locator, Page, Playwright


class ExecutionError(RuntimeError):
    pass


async def _locator(page: "Page", target: Target) -> "Locator":
    """One element, or an explanation. See :mod:`qa_copilot.executor.resolver`."""
    return await resolve(page, target)


@dataclass
class BrowserSession:
    session_id: str
    environment: Environment
    page: "Page"
    browser: "Browser"
    playwright: "Playwright"
    artifact_dir: Path
    authenticated_as: str | None = None
    _secret_targets: list[Target] = field(default_factory=list)
    _new_pages: list["Page"] = field(default_factory=list)
    _window_stack: list["Page"] = field(default_factory=list)

    # --- windows the app opens itself -------------------------------------
    def watch_for_popups(self) -> None:
        """Record every window the application opens.

        Apps that show a document in ``window.open`` would otherwise be
        untestable: the click succeeds, the popup carries the content, and every
        assertion afterwards runs against the untouched opener — passing or
        failing for reasons that have nothing to do with the document.
        """
        try:
            self.page.context.on("page", self._new_pages.append)
        except Exception:
            pass

    async def _follow_popup(self, timeout_ms: int = 700) -> str | None:
        """Make the window the last click opened the page steps run against."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self._new_pages:
                popup = self._new_pages.pop()
                self._new_pages.clear()
                try:
                    await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass  # a slow popup is still the right page to be on
                self._window_stack.append(self.page)
                self.page = popup
                return popup.url
            await asyncio.sleep(0.05)
        return None

    async def back_to_opener(self) -> str | None:
        """Return to the window that opened the current one."""
        if not self._window_stack:
            return None
        popup, self.page = self.page, self._window_stack.pop()
        try:
            await popup.close()
        except Exception:
            pass
        return self.page.url

    # --- navigation & interaction ----------------------------------------
    def url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    async def navigate(self, path: str) -> str:
        url = path if path.startswith("http") else self.environment.base_url.rstrip("/") + "/" + path.lstrip("/")
        await self.page.goto(url, wait_until="domcontentloaded")
        return self.page.url

    async def click(self, target: Target, timeout_ms: int = 10_000) -> str | None:
        locator = await _locator(self.page, target)
        await locator.first.click(timeout=timeout_ms)
        return await self._follow_popup()

    async def fill(self, target: Target, value: str, timeout_ms: int = 10_000) -> None:
        locator = await _locator(self.page, target)
        await locator.first.fill(value, timeout=timeout_ms)

    async def fill_secret(self, target: Target, secret: SecretValue, timeout_ms: int = 10_000) -> None:
        """Type a secret and remember the field so screenshots mask it."""
        self._secret_targets.append(target)
        locator = await _locator(self.page, target)
        await locator.first.fill(secret.reveal(), timeout=timeout_ms)

    async def select(self, target: Target, option: str, timeout_ms: int = 10_000) -> None:
        locator = await _locator(self.page, target)
        await locator.first.select_option(option, timeout=timeout_ms)

    async def wait_for(
        self, target: Target | None = None, url_contains: str | None = None, timeout_ms: int = 10_000
    ) -> None:
        if target is not None:
            locator = await _locator(self.page, target)
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
        if url_contains is not None:
            await self.page.wait_for_url(f"**{url_contains}**", timeout=timeout_ms)

    # --- assertions -------------------------------------------------------
    async def visibility(self, target: Target, timeout_ms: int = 5_000) -> tuple[str, str]:
        """('visible' | 'hidden' | 'missing', explanation).

        Assertions need the three-way answer: "I could not find that at all, and
        here is what is on the page" is a different — and far more useful —
        message than "it was not visible".
        """
        try:
            locator = await _locator(self.page, target)
        except ResolutionError as exc:
            return "missing", str(exc)
        try:
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
            return "visible", ""
        except Exception:
            return "hidden", "I found it, but it is not visible on the page"

    async def is_visible(self, target: Target, timeout_ms: int = 5_000) -> bool:
        try:
            locator = await _locator(self.page, target)
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except ResolutionError:
            # "not found" is a legitimate answer to "is this visible?"
            return False
        except Exception:
            return False

    async def text_present(self, expected: str, timeout_ms: int = 5_000) -> bool:
        try:
            await self.page.get_by_text(expected).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    # --- authentication ---------------------------------------------------
    async def login(self, recipe: LoginRecipe, creds: Credentials) -> dict[str, Any]:
        """Drive the configured login form. The plan never described these steps
        and never saw either value."""
        await self.navigate(recipe.path)
        self._secret_targets.extend([recipe.username_target, recipe.password_target])
        username = await _locator(self.page, recipe.username_target)
        await username.first.fill(creds.username.reveal())
        password = await _locator(self.page, recipe.password_target)
        await password.first.fill(creds.password.reveal())
        for name, target in recipe.extra_targets.items():
            value = creds.extras.get(name)
            if value is None:
                raise RuntimeError(
                    f"the login form for this environment needs a {name!r} value, but "
                    f"identity {creds.identity!r} has no extra_refs entry for {name!r}"
                )
            self._secret_targets.append(target)
            extra = await _locator(self.page, target)
            await extra.first.fill(value.reveal())
        submit = await _locator(self.page, recipe.submit_target)
        await submit.first.click()

        ok = True
        detail = "submitted"
        if recipe.success_url_contains:
            try:
                await self.page.wait_for_url(f"**{recipe.success_url_contains}**", timeout=10_000)
                detail = "redirected to expected URL"
            except Exception:
                ok = False
                detail = f"did not reach a URL containing {recipe.success_url_contains!r}"
        if ok and recipe.success_target is not None:
            ok = await self.is_visible(recipe.success_target)
            detail = "success indicator visible" if ok else "success indicator not found"
        if not ok and recipe.failure_target is not None and await self.is_visible(recipe.failure_target, 1_000):
            detail = "login form reported an error"

        if ok:
            self.authenticated_as = creds.identity
        return {
            "authenticated": ok,
            "identity": creds.identity,
            "detail": detail,
            "url": self.page.url,
            "secret_values_exposed": False,
        }

    # --- artifacts (always sanitised) -------------------------------------
    async def screenshot(self, name: str = "screenshot") -> str:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{name}-{uuid.uuid4().hex[:8]}.png"
        masks = []
        for secret_target in self._secret_targets:
            try:
                masks.append((await _locator(self.page, secret_target)).first)
            except Exception:
                continue  # the field is gone; the type=password mask below still applies
        # Password inputs are masked whether or not we were the one to fill them.
        masks.append(self.page.locator("input[type=password]"))
        try:
            await self.page.screenshot(path=str(path), mask=masks, full_page=False)
        except Exception:
            await self.page.screenshot(path=str(path), full_page=False)
        return str(path)

    async def _eval(self, script: str, default: Any) -> Any:
        """Page evaluation that never turns a test failure into a crash."""
        try:
            return await self.page.evaluate(script)
        except Exception:
            return default

    async def snapshot(self, max_chars: int = 4_000) -> dict[str, Any]:
        """A scrubbed, bounded view of the page for the model to reason about."""
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        body = await self._eval(
            "() => document.body ? document.body.innerText : ''", ""
        )
        headings = await self._eval(
            "() => Array.from(document.querySelectorAll('h1,h2,h3')).slice(0,20).map(e => e.innerText.trim())",
            [],
        )
        controls = await self._eval(
            """() => Array.from(document.querySelectorAll('a,button,input,select,[role=button]'))
                    .slice(0, 60)
                    .map(e => ({
                        tag: e.tagName.toLowerCase(),
                        type: e.getAttribute('type') || null,
                        name: e.getAttribute('name') || null,
                        testid: e.getAttribute('data-testid') || null,
                        label: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim().slice(0, 60)
                    }))""",
            [],
        )
        # Values are never read back off the page; only structure and labels.
        return sanitizer.scrub(
            {
                "url": self.page.url,
                "title": title,
                "headings": headings,
                "controls": controls,
                "text": (body or "")[:max_chars],
                "truncated": len(body or "") > max_chars,
            }
        )

    async def close(self) -> None:
        try:
            await self.browser.close()
        except Exception:
            pass
        finally:
            try:
                await self.playwright.stop()
            except Exception:
                pass


async def open_session(
    environment: Environment,
    artifact_dir: Path,
    *,
    headless: bool = True,
    browser_name: str = "chromium",
) -> BrowserSession:
    from playwright.async_api import async_playwright

    if browser_name not in {"chromium", "firefox", "webkit"}:
        raise ExecutionError(f"unsupported browser {browser_name!r}")

    pw = await async_playwright().start()
    browser = None
    try:
        launcher = getattr(pw, browser_name)
        browser = await launcher.launch(headless=headless)
        context = await browser.new_context(ignore_https_errors=not environment.verify_tls)
        page = await context.new_page()
    except BaseException:
        # Otherwise a half-built session leaves a browser process running.
        if browser is not None:
            await browser.close()
        await pw.stop()
        raise

    session = BrowserSession(
        session_id=uuid.uuid4().hex[:12],
        environment=environment,
        page=page,
        browser=browser,
        playwright=pw,
        artifact_dir=artifact_dir,
    )
    session.watch_for_popups()
    return session
