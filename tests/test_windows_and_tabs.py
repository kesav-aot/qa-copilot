"""Following a window the application opened.

The bug this file exists for: popup handling never worked. It subscribed with
``context.on("page", list.append)``, which raises — Playwright sets an attribute
on the handler and a built-in method has no ``__dict__`` — inside a bare
``except Exception: pass``. So no window was ever recorded, the click reported
success, and every later step ran against the untouched opener. A document popup
and a shop's target=_blank product link failed the same way, invisibly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qa_copilot.dsl.schema import Target, TestPlan
from qa_copilot.executor.browser import open_session


def test_the_action_exists_in_the_dsl():
    plan = TestPlan.model_validate(
        {
            "version": 1,
            "name": "x",
            "environment": "demo",
            "steps": [{"action": "switch_window"}, {"action": "switch_window", "to": "previous"}],
        }
    )
    assert [s.to for s in plan.steps] == ["new", "previous"]


def test_plain_english_compiles_both_directions(copilot):
    result = copilot.compile_plain(
        "# t\nEnvironment: demo\n\nSwitch to the new tab\nGo back to the previous tab\n"
    )
    test = result["tests"][0]
    assert test["understood"], test
    assert [s["action"] for s in test["plan"]["steps"]] == ["switch_window", "switch_window"]
    assert test["plan"]["steps"][1]["to"] == "previous"


def test_the_phrasebook_lists_it(copilot):
    words = copilot.phrasebook() if hasattr(copilot, "phrasebook") else None
    from qa_copilot.plain.phrasebook import phrasebook

    text = str(phrasebook())
    assert "Switch to the new window" in text or "Switch to the new tab" in text


async def test_a_target_blank_link_is_followed(copilot, demo_server):
    """The Amazon shape: an ordinary link with target=_blank."""
    session = await open_session(copilot.config.environment("demo"), Path("artifacts"))
    try:
        await session.navigate("/listing")
        await session.click(Target(testid="first-result"))
        assert "/listing" in session.page.url, "a click must not switch windows by itself"

        url = await session.switch_to_new_window(5_000)
        assert url is not None, "the new tab was not found"
        assert "/detail" in session.page.url

        back = await session.back_to_opener()
        assert back is not None
        assert "/listing" in session.page.url
    finally:
        await session.close()


async def test_switching_when_nothing_opened_is_reported(copilot, demo_server):
    """Silence here is what cost two runs: the step must say it found nothing."""
    session = await open_session(copilot.config.environment("demo"), Path("artifacts"))
    try:
        await session.navigate("/listing")
        assert await session.switch_to_new_window(300) is None
    finally:
        await session.close()


async def test_a_click_no_longer_switches_windows_on_its_own(copilot, demo_server):
    """It used to try for 700ms, so whether later steps ran in the new window
    depended on how fast it happened to open, and the report looked the same
    either way."""
    session = await open_session(copilot.config.environment("demo"), Path("artifacts"))
    try:
        await session.navigate("/listing")
        assert await session.click(Target(testid="first-result")) is None
        assert "/listing" in session.page.url
    finally:
        await session.close()
