"""`wait_for` must wait for an element that is not on the page yet.

Resolution is done against the live page, so resolving once means "wait for X
to appear" fails instantly whenever X has not rendered — which is the only case
anybody writes a wait for. Regression test for an inbox whose grid is fetched
after the page load.
"""

import asyncio
import time

import pytest

from qa_copilot.dsl.schema import Target
from qa_copilot.executor import browser as B
from qa_copilot.executor.resolver import ResolutionError


class _First:
    async def wait_for(self, state=None, timeout=None):
        return None


class _Locator:
    def __init__(self):
        self.first = _First()


def _session():
    session = B.BrowserSession.__new__(B.BrowserSession)
    session.page = object()
    return session


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_waits_for_an_element_that_appears_later(monkeypatch):
    calls = {"n": 0}

    async def flaky(page, target):
        calls["n"] += 1
        if calls["n"] < 4:
            raise ResolutionError('I could not find "Processed by AI"')
        return _Locator()

    monkeypatch.setattr(B, "_locator", flaky)
    started = time.monotonic()
    _run(_session().wait_for(Target(describe="the robot icon"), timeout_ms=5_000))

    assert calls["n"] == 4, "resolution should be retried until the element shows up"
    assert time.monotonic() - started >= 0.7, "it should really have waited between tries"


def test_still_reports_the_resolver_message_when_it_never_appears(monkeypatch):
    async def never(page, target):
        raise ResolutionError('I could not find "Nope"\n  links you can use here: "Help"')

    monkeypatch.setattr(B, "_locator", never)
    started = time.monotonic()
    with pytest.raises(ResolutionError) as excinfo:
        _run(_session().wait_for(Target(describe="nope"), timeout_ms=1_000))

    assert "links you can use here" in str(excinfo.value), (
        "the timeout must not cost the human the 'here is what IS on the page' explanation"
    )
    assert time.monotonic() - started >= 0.9, "the whole timeout should be spent before giving up"
