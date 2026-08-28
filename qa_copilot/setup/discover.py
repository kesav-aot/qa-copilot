"""Look at a real sign-in page and work out how to drive it.

Writing a login recipe by hand means writing selectors, which is the one
developer-shaped step left in setting QA Copilot up. This removes it: open the
page, find the fields, and pick the most durable way to refer to each one.

Nothing here ever handles a credential. It only reports *where* the fields are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qa_copilot.dsl.schema import Target

# Ordered best-to-worst. A test id survives a redesign; a CSS id usually does not.
_FIND_ELEMENT = """
(el) => {
    if (!el) return null;
    let label = null;
    if (el.labels && el.labels.length) label = el.labels[0].innerText.trim();
    if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
    return {
        testid: el.getAttribute('data-testid') || el.getAttribute('data-test')
                || el.getAttribute('data-cy'),
        label: label,
        placeholder: el.getAttribute('placeholder'),
        name: el.getAttribute('name'),
        id: el.getAttribute('id'),
        text: (el.innerText || el.value || '').trim().slice(0, 40),
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type'),
    };
}
"""


# Every visible field in the sign-in form that is not the username, the password
# or the button. Same shape as _FIND_ELEMENT so _target_for can consume it.
_FIND_EXTRA_FIELDS = """
() => {
    const pw = document.querySelector('input[type=password]');
    if (!pw) return [];
    const form = pw.closest('form') || document;
    const skip = ['hidden','submit','button','checkbox','radio','image','reset'];
    const inputs = Array.from(form.querySelectorAll('input, textarea, select'))
        .filter(i => !skip.includes(i.type));
    const before = inputs.slice(0, inputs.indexOf(pw)).reverse();
    const user = before.find(i => ['email','text','tel'].includes(i.type) || !i.type)
                 || before[0] || null;
    return inputs
        .filter(i => i !== pw && i !== user)
        .filter(i => i.offsetParent !== null)
        .map(el => {
            let label = null;
            if (el.labels && el.labels.length) label = el.labels[0].innerText.trim();
            if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
            return {
                testid: el.getAttribute('data-testid') || el.getAttribute('data-test')
                        || el.getAttribute('data-cy'),
                label: label,
                placeholder: el.getAttribute('placeholder'),
                name: el.getAttribute('name'),
                id: el.getAttribute('id'),
                text: (el.innerText || el.value || '').trim().slice(0, 40),
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type'),
            };
        });
}
"""


@dataclass
class Found:
    target: Target
    how: str
    detail: dict[str, Any]

    def describe(self) -> str:
        return f"{self.how}"


@dataclass
class LoginDiscovery:
    url: str
    username: Found | None = None
    password: Found | None = None
    submit: Found | None = None
    extras: dict[str, Found] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    inventory: dict[str, list[str]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all([self.username, self.password, self.submit])


def _target_for(info: dict[str, Any], *, is_button: bool) -> Found:
    """Pick the most durable way to refer to this element."""
    if info.get("testid"):
        return Found(Target(testid=info["testid"]), f'test id "{info["testid"]}"', info)
    if is_button and info.get("text"):
        return Found(
            Target(role="button", name=info["text"]), f'button labelled "{info["text"]}"', info
        )
    if info.get("label"):
        return Found(Target(label=info["label"]), f'field labelled "{info["label"]}"', info)
    if info.get("placeholder"):
        return Found(
            Target(describe=info["placeholder"]), f'placeholder "{info["placeholder"]}"', info
        )
    if info.get("name"):
        return Found(
            Target(css=f'[name="{info["name"]}"]'), f'name attribute "{info["name"]}"', info
        )
    if info.get("id"):
        return Found(Target(css=f'#{info["id"]}'), f'id "{info["id"]}"', info)
    return Found(Target(css=info.get("tag", "input")), "position on the page (fragile)", info)


async def discover_login(page, url: str) -> LoginDiscovery:
    """Open ``url`` and find the username field, password field and submit button."""
    from qa_copilot.executor.resolver import page_inventory

    result = LoginDiscovery(url=url)
    await page.goto(url, wait_until="domcontentloaded")

    password_el = page.locator("input[type=password]").first
    if await password_el.count() == 0:
        result.problems.append(
            "I could not find a password field on that page. If sign-in is behind "
            "a button or a cookie banner, open the page yourself and give me the "
            "address of the form itself."
        )
        result.inventory = await page_inventory(page)
        return result

    result.password = _target_for(await password_el.evaluate(_FIND_ELEMENT), is_button=False)

    # The username field is the text-ish input just before the password one.
    username_handle = await page.evaluate_handle(
        """() => {
            const pw = document.querySelector('input[type=password]');
            if (!pw) return null;
            const form = pw.closest('form') || document;
            const inputs = Array.from(form.querySelectorAll('input, textarea'))
                .filter(i => !['hidden','submit','button','checkbox','radio'].includes(i.type));
            const before = inputs.slice(0, inputs.indexOf(pw)).reverse();
            return before.find(i => ['email','text','tel'].includes(i.type) || !i.type)
                   || before[0] || null;
        }"""
    )
    username_info = await username_handle.evaluate(_FIND_ELEMENT)
    if username_info:
        result.username = _target_for(username_info, is_button=False)
    else:
        result.problems.append(
            "I found a password field but no username field before it. If the app "
            "asks for the username on a separate screen, that needs handling by "
            "hand — tell whoever set this up."
        )

    submit_info = await page.evaluate(
        """() => {
            const pw = document.querySelector('input[type=password]');
            const form = pw ? pw.closest('form') : null;
            const root = form || document;
            const wanted = /sign\\s*-?\\s*in|log\\s*-?\\s*in|continue|submit|next|enter/i;
            const buttons = Array.from(
                root.querySelectorAll('button, input[type=submit], [role=button]')
            );
            const byType = buttons.find(b => b.type === 'submit');
            const byText = buttons.find(b => wanted.test(b.innerText || b.value || ''));
            const el = byType || byText || buttons[0] || null;
            if (!el) return null;
            let label = (el.innerText || el.value || '').trim();
            return {
                testid: el.getAttribute('data-testid') || el.getAttribute('data-test'),
                label: null, placeholder: null,
                name: el.getAttribute('name'), id: el.getAttribute('id'),
                text: label.slice(0, 40), tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type'),
            };
        }"""
    )
    if submit_info:
        result.submit = _target_for(submit_info, is_button=True)
    else:
        result.problems.append("I could not find a sign-in button on that page.")

    # Anything else the form insists on — a PIN, a tenant, a clinic code. These
    # cannot be assumed in advance: the only way to know a form wants a third
    # credential is to look at it.
    for index, info in enumerate(await page.evaluate(_FIND_EXTRA_FIELDS)):
        name = _extra_name(info, index)
        if name in ("username", "password"):
            continue
        result.extras[name] = _target_for(info, is_button=False)

    if not result.complete:
        result.inventory = await page_inventory(page)
    return result


def _extra_name(info: dict[str, Any], index: int) -> str:
    """A short, stable key for a field, used in config and shown to a person."""
    import re

    for candidate in (info.get("name"), info.get("label"), info.get("placeholder"), info.get("id")):
        if candidate:
            slug = re.sub(r"[^a-z0-9]+", "_", str(candidate).strip().lower()).strip("_")
            if slug:
                return slug
    return f"field_{index + 2}"


async def find_error_target(page) -> Target | None:
    """After a failed sign-in, find the element that reports the error, so a wrong
    password fails fast instead of waiting for a timeout."""
    info = await page.evaluate(
        """() => {
            const sel = '[role=alert], .error, .alert, .invalid-feedback, [data-testid*=error],'
                      + '[class*=error], [id*=error]';
            for (const el of Array.from(document.querySelectorAll(sel))) {
                if (!el.getClientRects().length) continue;
                const text = (el.innerText || '').trim();
                if (!text || text.length > 200) continue;
                return {
                    testid: el.getAttribute('data-testid'),
                    text: text.slice(0, 60),
                };
            }
            return null;
        }"""
    )
    if not info:
        return None
    if info.get("testid"):
        return Target(testid=info["testid"])
    return Target(text=info["text"])
