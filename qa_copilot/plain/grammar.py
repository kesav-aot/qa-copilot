"""The phrase rules: ordinary English in, Test DSL steps out.

Every rule carries its own examples, and ``qa_copilot.plain.phrasebook`` builds
the user-facing reference from this list — so the documentation cannot drift
from what actually parses.

Rule order is significant. "Check the page shows X" must be tried before
"Check the X box", and "Go to the Users page" before "Click ...", or the wrong
rule wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

# --- shared fragments -------------------------------------------------------

Q = r"[\"'“”‘’]"
QUOTED = rf"{Q}(?P<what>[^\"'“”‘’]+){Q}"
END = r"\s*[.!]?\s*$"


def unquote(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip().strip(".!,")
    match = re.match(rf"^{Q}(.*){Q}$", text)
    return (match.group(1) if match else text).strip()


@dataclass
class Built:
    """What a rule produced: DSL steps, a plain explanation, maybe a warning."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    explain: str = ""
    warning: str | None = None
    error: str | None = None
    suggestion: str | None = None

    @classmethod
    def fail(cls, error: str, suggestion: str | None = None) -> "Built":
        return cls(error=error, suggestion=suggestion)


@dataclass
class Rule:
    name: str
    category: str
    pattern: re.Pattern[str]
    build: Callable[[re.Match[str], Any], Built]
    summary: str
    examples: list[str]


def _target(match: re.Match[str], key: str = "what") -> dict[str, Any]:
    """Build a late-binding target from a phrase, honouring 'in the X row'."""
    target: dict[str, Any] = {"describe": unquote(match.group(key))}
    within = match.groupdict().get("within")
    if within:
        target["within"] = unquote(within)
    return target


# --- authentication ---------------------------------------------------------

_CAPABILITY_PHRASE = re.compile(
    r"(?i)^(?:someone|a user|a person|anyone|an account)?\s*"
    r"(?:who|that)?\s*(?:can|has permission to|is able to)\s+(?P<cap>.+)$"
)


def _resolve_actor(who: str, ctx) -> tuple[dict[str, Any] | None, str, str | None]:
    """Turn 'an admin' / 'ADMIN_USER' / 'someone who can manage settings' into an
    authenticate step. Returns (step, explanation, error)."""
    raw = who.strip().strip(".")
    cleaned = re.sub(r"(?i)^(?:an?|the)\s+", "", raw).strip()

    # 1. An explicit alias, however they capitalised it.
    aliases = {a.upper(): a for a in ctx.identities}
    key = re.sub(r"[\s-]+", "_", cleaned).upper()
    if key in aliases:
        alias = aliases[key]
        return (
            {"action": "authenticate", "identity": alias},
            f"log in as {alias} — the password comes from the secret store, "
            f"it is never in this file",
            None,
        )

    # 2. "someone who can manage settings"
    phrase = _CAPABILITY_PHRASE.match(cleaned)
    if phrase:
        wanted = re.sub(r"[\s-]+", "_", phrase.group("cap").strip().strip(".")).lower()
        if wanted in ctx.capabilities:
            return (
                {"action": "authenticate", "capability": wanted},
                f"log in as whoever can {wanted.replace('_', ' ')}",
                None,
            )
        return (
            None,
            "",
            f'no test account can "{phrase.group("cap").strip()}". '
            f"Available: {ctx.capability_list()}",
        )

    # 3. A role word — "an admin", "a standard user", "a customer".
    for pattern, capabilities in _ROLE_WORDS:
        if pattern.search(cleaned):
            for capability in capabilities:
                if capability in ctx.capabilities:
                    return (
                        {"action": "authenticate", "capability": capability},
                        f"log in as {cleaned} (a test account that can "
                        f"{capability.replace('_', ' ')})",
                        None,
                    )

    return (
        None,
        "",
        f'I do not know who "{raw}" is.\n'
        f"  Test accounts you can use: {ctx.identity_list()}\n"
        f"  Or say what they need to do: \"log in as someone who can "
        f"{next(iter(sorted(ctx.capabilities)), 'browse')}\"",
    )


_ROLE_WORDS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?i)\badmin(istrator)?\b"), ["manage_users", "manage_settings", "admin"]),
    (re.compile(r"(?i)\bsuper ?user\b"), ["manage_settings", "admin"]),
    (re.compile(r"(?i)\b(standard|normal|regular|ordinary|basic)\b"), ["browse"]),
    (re.compile(r"(?i)\b(customer|shopper|buyer)\b"), ["create_order", "browse"]),
    (re.compile(r"(?i)\b(viewer|read[- ]only)\b"), ["browse"]),
    (re.compile(r"(?i)\buser\b"), ["browse"]),
]


def _build_login(match: re.Match[str], ctx) -> Built:
    step, explain, error = _resolve_actor(match.group("who"), ctx)
    if error:
        return Built.fail(error)
    return Built(steps=[step], explain=explain)  # type: ignore[list-item]


def _build_login_bare(match: re.Match[str], ctx) -> Built:
    return Built.fail(
        "say who to log in as",
        f'try "log in as an admin", or one of: {ctx.identity_list()}',
    )


def _build_logout(match: re.Match[str], ctx) -> Built:
    return Built(
        steps=[{"action": "click", "target": {"describe": "Sign out"}}],
        explain="click Sign out",
        warning='assumes the link is called "Sign out"; if it is not, say '
        '"click <whatever it says>" instead',
    )


# --- navigation -------------------------------------------------------------

def _to_path(where: str) -> tuple[str, bool]:
    """Return (path, was_guessed)."""
    text = unquote(where)
    if text.startswith(("http://", "https://", "/")):
        return text, False
    cleaned = re.sub(r"(?i)\b(the|a|an|page|screen|tab|section|view)\b", " ", text)
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").lower()
    return "/" + cleaned, True


def _build_navigate(match: re.Match[str], ctx) -> Built:
    path, guessed = _to_path(match.group("where"))
    return Built(
        steps=[{"action": "navigate", "path": path}],
        explain=f"go to {path}",
        warning=(
            f"I worked out {path!r} from your wording — I will find out at run time "
            f"whether that page exists. If it lives somewhere else, write the "
            f'address itself, e.g. "Go to /admin/users"'
            if guessed
            else None
        ),
    )


# --- interaction ------------------------------------------------------------

_PASSWORDY = re.compile(r"(?i)\b(password|passwd|pwd|api[_-]?key|token|secret)\b")


_KIND_WORDS = {
    "button", "buttons", "link", "links", "field", "fields", "box", "boxes",
    "checkbox", "check box", "dropdown", "drop down", "select", "tab", "tabs",
    "heading", "title", "icon", "menu", "menu item", "option", "input", "row",
    "page", "screen", "form", "toggle", "switch", "textarea",
}


def _build_click(match: re.Match[str], ctx) -> Built:
    groups = match.groupdict()
    within = (groups.get("within") or "").strip().lower().strip(".")
    if within in _KIND_WORDS:
        # "Click the Sign in button" — the preposition is part of the label.
        whole = f"{groups.get('what', '')} {groups.get('within', '')}".strip()
        return Built(
            steps=[{"action": "click", "target": {"describe": whole}}],
            explain=f"click {whole}",
        )
    target = _target(match)
    if not target["describe"]:
        return Built.fail("say what to click", 'e.g. "click the Save button"')
    return Built(
        steps=[{"action": "click", "target": target}],
        explain=f"click {target['describe']}"
        + (f" in the {target['within']!r} row" if target.get("within") else ""),
    )


def _build_type(match: re.Match[str], ctx) -> Built:
    value = unquote(match.group("value"))
    target = _target(match, "field")
    if _PASSWORDY.search(target["describe"]) or _PASSWORDY.search(value):
        return Built.fail(
            "that looks like a credential, and credentials must never be written "
            "in a test file",
            'use "log in as <who>" instead — the real value is fetched from the '
            "secret store at the moment it is needed",
        )
    return Built(
        steps=[{"action": "fill", "target": target, "value": value}],
        explain=f"type {value!r} into {target['describe']}",
    )


def _build_select(match: re.Match[str], ctx) -> Built:
    return Built(
        steps=[
            {
                "action": "select",
                "target": _target(match, "field"),
                "option": unquote(match.group("value")),
            }
        ],
        explain=f"choose {unquote(match.group('value'))!r} from "
        f"{unquote(match.group('field'))}",
    )


def _build_tick(match: re.Match[str], ctx) -> Built:
    target = _target(match)
    return Built(
        steps=[{"action": "click", "target": target}],
        explain=f"tick {target['describe']}",
    )


# --- waiting ----------------------------------------------------------------

def _build_wait_text(match: re.Match[str], ctx) -> Built:
    what = unquote(match.group("what"))
    return Built(
        steps=[{"action": "wait_for", "target": {"describe": what}, "timeout_ms": 15_000}],
        explain=f"wait for {what!r} to appear (up to 15s)",
    )


def _build_wait_seconds(match: re.Match[str], ctx) -> Built:
    seconds = min(int(match.group("n")), 30)
    return Built(
        steps=[{"action": "pause", "seconds": seconds}],
        explain=f"wait {seconds} seconds",
        warning="a fixed wait makes tests slow and flaky. Prefer "
        '"wait for <something> to appear"',
    )


# --- assertions -------------------------------------------------------------

def _build_check_text(match: re.Match[str], ctx) -> Built:
    what = unquote(match.group("what"))
    return Built(
        steps=[{"action": "assert", "kind": "text", "expected": what}],
        explain=f"check the page shows {what!r}",
    )


def _build_check_visible(match: re.Match[str], ctx) -> Built:
    target = _target(match)
    return Built(
        steps=[{"action": "assert", "kind": "visible", "target": target}],
        explain=f"check {target['describe']} is on the page",
    )


def _build_check_not_visible(match: re.Match[str], ctx) -> Built:
    target = _target(match)
    return Built(
        steps=[{"action": "assert", "kind": "not_visible", "target": target}],
        explain=f"check {target['describe']} is NOT on the page",
    )


def _build_check_url(match: re.Match[str], ctx) -> Built:
    expected = unquote(match.group("what"))
    return Built(
        steps=[{"action": "assert", "kind": "url_contains", "expected": expected}],
        explain=f"check the web address contains {expected!r}",
    )


def _build_check_status(match: re.Match[str], ctx) -> Built:
    code = int(match.group("code"))
    return Built(
        steps=[{"action": "assert", "kind": "status", "expected": code}],
        explain=f"check the last API call returned {code}",
    )


# --- api --------------------------------------------------------------------

def _build_api(match: re.Match[str], ctx) -> Built:
    method = match.group("method").upper()
    path = match.group("path").rstrip(".,;")
    step: dict[str, Any] = {"action": "api_request", "method": method, "path": path}
    explain = f"call {method} {path}"

    who = match.groupdict().get("who")
    if who:
        actor, _, error = _resolve_actor(who, ctx)
        if error:
            return Built.fail(error)
        if actor and actor.get("identity"):
            step["identity"] = actor["identity"]
            explain += f" as {actor['identity']}"
        elif actor and actor.get("capability"):
            # Keep the capability rather than pinning an alias: the plan stays
            # portable, and the broker applies least privilege at run time.
            step["capability"] = actor["capability"]
            chosen = ctx.identity_for_capability(actor["capability"])
            explain += (
                f" as {chosen}" if chosen
                else f" as whoever can {actor['capability'].replace('_', ' ')}"
            )

    code = match.groupdict().get("code")
    if code:
        step["expect_status"] = int(code)
        explain += f", expecting {code}"
    return Built(steps=[step], explain=explain)


# --- misc -------------------------------------------------------------------

def _build_screenshot(match: re.Match[str], ctx) -> Built:
    name = unquote(match.groupdict().get("name") or "") or "screenshot"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "screenshot"
    return Built(steps=[{"action": "screenshot", "name": slug}], explain=f"take a screenshot ({slug})")


def _build_note(match: re.Match[str], ctx) -> Built:
    return Built(steps=[], explain=f"note: {match.group('text').strip()}")


# --- the rule table ---------------------------------------------------------
#
# Order matters. Assertions come before actions so "Check the page shows X" is
# not read as "Check the X box"; navigation comes before clicking.

RULES: list[Rule] = [
    Rule(
        "note", "Notes",
        re.compile(r"(?i)^(?:note|comment|reminder)\s*[:\-]\s*(?P<text>.+)$"),
        _build_note,
        "A comment. Recorded in the report, does nothing.",
        ["Note: this covers ticket QA-4417"],
    ),
    # --- authentication
    Rule(
        "log in", "Signing in",
        re.compile(
            r"(?i)^(?:log|sign)\s*-?\s*in"
            r"(?:\s+to\s+(?:the\s+)?\S+)?\s+as\s+(?P<who>.+?)" + END
        ),
        _build_login,
        "Sign in as a test account. Never write a password — name the person or "
        "what they need to be able to do.",
        [
            "Log in as an admin",
            "Log in as ADMIN_USER",
            "Sign in as a standard user",
            "Log in as someone who can manage settings",
        ],
    ),
    Rule(
        "log in (no actor)", "Signing in",
        re.compile(r"(?i)^(?:log|sign)\s*-?\s*in" + END),
        _build_login_bare,
        "Rejected — say who.",
        [],
    ),
    Rule(
        "log out", "Signing in",
        re.compile(r"(?i)^(?:log|sign)\s*-?\s*out" + END),
        _build_logout,
        "Click the Sign out link.",
        ["Log out"],
    ),
    # --- assertions (before actions)
    Rule(
        "check status", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert)?\s*(?:that\s+)?"
            r"(?:the\s+)?(?:response|request|call|api)?\s*"
            r"(?:status|status code|response code)\s*(?:code\s*)?"
            r"(?:is|was|should be|equals|=)?\s*(?P<code>\d{3})" + END
        ),
        _build_check_status,
        "Check the status code of the most recent API call.",
        ["Check the status is 403", "The response status should be 200"],
    ),
    Rule(
        "check url", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert)?\s*(?:that\s+)?"
            r"(?:the\s+)?(?:url|address|link|page address)\s+"
            r"(?:should\s+)?(?:contains?|includes?|is|ends with|has)\s+(?P<what>.+?)" + END
        ),
        _build_check_url,
        "Check the browser address bar.",
        ["Check the URL contains /users", "The URL should contain /dashboard"],
    ),
    Rule(
        "check not visible", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert)?\s*(?:that\s+)?"
            r"(?:i\s+|we\s+|the user\s+)?(?:should\s+not|can\s?not|cannot|do\s+not|does\s+not|is\s+not|are\s+not)"
            r"\s+(?:see|show|display|be)\w*\s+(?P<what>.+?)"
            r"(?:\s+in\s+the\s+(?P<within>.+?)\s+row)?" + END
        ),
        _build_check_not_visible,
        "Check something is absent.",
        ["Check I should not see the Delete button", "Verify that Disable is not shown"],
    ),
    Rule(
        "check not shown", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert)?\s*(?:that\s+)?"
            r"(?P<what>.+?)\s+(?:is|are)\s+(?:not|no longer)\s+"
            r"(?:shown|displayed|visible|present|there)" + END
        ),
        _build_check_not_visible,
        "Check something is absent.",
        ['Check "Disable" is no longer shown', "Verify the Save button is not visible"],
    ),
    Rule(
        "check page shows", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert|make sure)?\s*(?:that\s+)?"
            r"(?:the\s+)?(?:page|screen|it|result|response|table|list)\s+"
            r"(?:should\s+)?(?:shows?|says?|displays?|contains?|includes?|reads?)\s+"
            r"(?P<what>.+?)" + END
        ),
        _build_check_text,
        "Check some text appears anywhere on the page.",
        [
            'Check the page shows "User Management"',
            'The page should say "Order placed"',
        ],
    ),
    Rule(
        "check text displayed", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert|make sure)\s*(?:that\s+)?"
            r"(?P<what>.+?)\s+(?:is|are)\s+"
            r"(?:shown|displayed|present|there|visible on the page)" + END
        ),
        _build_check_text,
        "Check some text appears.",
        ['Verify "Access denied" is displayed'],
    ),
    Rule(
        "check i see", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert|make sure)?\s*(?:that\s+)?"
            r"(?:i|we|the user)\s+(?:can\s+)?(?:should\s+)?(?:see|sees)\s+(?P<what>.+?)"
            r"(?:\s+in\s+the\s+(?P<within>.+?)\s+row)?" + END
        ),
        _build_check_visible,
        "Check an element is on the page.",
        ["Check I can see the Save button", "Verify I see Sign out"],
    ),
    Rule(
        "check visible", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert|make sure)\s*(?:that\s+)?"
            r"(?P<what>.+?)\s+(?:is|are)\s+visible" + END
        ),
        _build_check_visible,
        "Check an element is on the page.",
        ["Check the Users heading is visible"],
    ),
    Rule(
        "check bare text", "Checking",
        re.compile(
            r"(?i)^(?:check|verify|confirm|expect|assert|make sure)\s+(?:that\s+)?"
            + QUOTED + END
        ),
        lambda m, ctx: _build_check_text(m, ctx),
        "Check some exact text appears.",
        ['Check "Orders today"'],
    ),
    # --- api
    Rule(
        "api call", "API",
        re.compile(
            r"(?i)^(?:call|send|make|issue|do|perform)?\s*(?:an?\s+)?"
            r"(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD)\s+(?:request\s+)?(?:to\s+)?"
            r"(?P<path>/\S+)"
            r"(?:\s+as\s+(?P<who>.+?))?"
            r"(?:\s*(?:and|,)?\s*(?:it\s+)?(?:should\s+)?(?:returns?|gives?|expect(?:ing)?)\s+(?P<code>\d{3}))?"
            + END
        ),
        _build_api,
        "Call an API endpoint. The auth token is attached by the runner; you never "
        "see it or write it.",
        [
            "Call GET /api/users as an admin",
            "GET /api/users should return 200",
            "DELETE /api/orders/1 as an admin, expecting 204",
        ],
    ),
    # --- waiting
    Rule(
        "wait for", "Waiting",
        re.compile(
            r"(?i)^wait\s+(?:for|until)\s+(?:the\s+)?(?:page\s+(?:to\s+)?(?:shows?|says?|displays?)\s+)?"
            r"(?P<what>.+?)(?:\s+(?:to\s+)?appears?)?" + END
        ),
        _build_wait_text,
        "Wait for something to show up, up to 15 seconds.",
        ['Wait for the page to show "Done"', "Wait for the Save button"],
    ),
    Rule(
        "wait seconds", "Waiting",
        re.compile(r"(?i)^(?:wait|pause|sleep)\s+(?:for\s+)?(?P<n>\d+)\s*(?:s|secs?|seconds?)?" + END),
        _build_wait_seconds,
        "Wait a fixed number of seconds. Discouraged — prefer waiting for something.",
        ["Wait 2 seconds"],
    ),
    # --- navigation (before click)
    Rule(
        "navigate", "Moving around",
        re.compile(
            r"(?i)^(?:go\s+to|navigate\s+to|open|visit|browse\s+to|load)\s+(?P<where>.+?)" + END
        ),
        _build_navigate,
        "Go to a page. Give the address if you know it; a name will be guessed at.",
        ["Go to /users", "Open the Dashboard page", "Visit https://example.com/help"],
    ),
    # --- forms
    Rule(
        "select from", "Filling in forms",
        re.compile(
            r"(?i)^(?:select|choose|pick|set)\s+(?P<value>.+?)\s+"
            r"(?:from|in)\s+(?P<field>.+?)" + END
        ),
        _build_select,
        "Pick an option from a dropdown.",
        ['Select "Premium" from the Plan dropdown', "Choose United Kingdom from Country"],
    ),
    Rule(
        "type into", "Filling in forms",
        re.compile(
            r"(?i)^(?:type|enter|input|write|put)\s+(?P<value>.+?)\s+"
            r"(?:into|in)\s+(?P<field>.+?)" + END
        ),
        _build_type,
        "Type into a field. Never a password — use 'log in as'.",
        ['Type "blue widget" into the Search box', "Enter 2 in the Quantity field"],
    ),
    Rule(
        "fill with", "Filling in forms",
        re.compile(
            r"(?i)^(?:fill\s+(?:in\s+)?|set\s+)(?P<field>.+?)\s+"
            r"(?:with|to|=)\s+(?P<value>.+?)" + END
        ),
        _build_type,
        "Type into a field, named first.",
        ['Fill in Email with someone@example.com', 'Set Quantity to 3'],
    ),
    Rule(
        "tick", "Filling in forms",
        re.compile(
            r"(?i)^(?:tick|untick|toggle|switch\s+on|switch\s+off|turn\s+on|turn\s+off)\s+"
            r"(?:the\s+)?(?P<what>.+?)(?:\s+(?:check\s?box|box|toggle|switch))?" + END
        ),
        _build_tick,
        "Toggle a checkbox or switch.",
        ['Tick "Remember me"', "Turn on Email notifications"],
    ),
    Rule(
        "check the box", "Filling in forms",
        re.compile(
            r"(?i)^check\s+(?:the\s+)?(?P<what>.+?)\s+(?:check\s?box|box|toggle|switch)" + END
        ),
        _build_tick,
        "Toggle a checkbox. ('Tick' is clearer — 'check' also starts an assertion.)",
        ['Check the "Remember me" checkbox'],
    ),
    # --- double-clicking (before plain clicking) ------------------------
    Rule(
        "double click in named row", "Clicking",
        re.compile(
            r"(?i)^(?:double[-\s]?click|dbl[-\s]?click)\s+(?:on\s+)?(?P<what>.+?)\s+"
            r"(?:in|on|of|from|within)\s+(?:the\s+)?(?P<within>.+?)\s+row" + END
        ),
        lambda m, ctx: Built(
            steps=[{"action": "double_click", "target": _target(m)}],
            explain=f"double-click {_target(m)['describe']} in the '{unquote(m.group('within'))}' row",
        ),
        "Double-click something inside a particular row.",
        ['Double-click "ln1, fn1" in the URGENT row'],
    ),
    Rule(
        "double click", "Clicking",
        re.compile(
            r"(?i)^(?:double[-\s]?click|dbl[-\s]?click)\s+(?:on\s+)?(?P<what>.+?)" + END
        ),
        lambda m, ctx: Built(
            steps=[{"action": "double_click", "target": {"describe": unquote(m.group("what"))}}],
            explain=f'double-click {unquote(m.group("what"))}',
        ),
        "Double-click something. Needed for grids and rows that ignore a single click.",
        ['Double-click the "ln1, fn1" row', "Double-click the first row"],
    ),
    # --- clicking (last: the most generic)
    Rule(
        "click quoted", "Clicking",
        re.compile(
            r"(?i)^(?:click|press|tap|hit)\s+(?:on\s+)?(?:the\s+)?" + QUOTED
            + r"(?:\s+(?:button|link|tab|option))?" + END
        ),
        lambda m, ctx: Built(
            steps=[{"action": "click", "target": {"describe": unquote(m.group("what"))}}],
            explain=f'click "{unquote(m.group("what"))}"',
        ),
        "Click something whose label you have quoted exactly. Quote it when the "
        'label contains a word like "for" or "in".',
        ['Click "Save for later"'],
    ),
    # Two separate rules, because "in" is treacherous: without the mandatory
    # trailing "row", "Click the Sign in button" parses as what="the Sign",
    # within="button".
    Rule(
        "click in named row", "Clicking",
        re.compile(
            r"(?i)^(?:click|press|tap|hit|choose|select)\s+(?:on\s+)?(?P<what>.+?)\s+"
            r"(?:in|on|of|from|within)\s+(?:the\s+)?(?P<within>.+?)\s+row" + END
        ),
        _build_click,
        "Click something inside a particular row.",
        ['Click "Edit" in the Kit Osei row'],
    ),
    Rule(
        "click for row", "Clicking",
        re.compile(
            r"(?i)^(?:click|press|tap|hit|choose|select)\s+(?:on\s+)?(?P<what>.+?)\s+"
            r"for\s+(?:the\s+)?(?P<within>.+?)" + END
        ),
        _build_click,
        "Click something belonging to one record — the usual way to act on a "
        "single row in a table.",
        ["Click Disable for Rae Rivera"],
    ),
    Rule(
        "click", "Clicking",
        re.compile(r"(?i)^(?:click|press|tap|hit)\s+(?:on\s+)?(?P<what>.+?)" + END),
        _build_click,
        "Click a button, link, or anything else.",
        ['Click the Save button', 'Click "Add to cart"', "Click the second Disable button"],
    ),
    # --- misc
    Rule(
        "screenshot", "Evidence",
        re.compile(
            r"(?i)^(?:take|capture|grab|save)\s+(?:a\s+)?(?:screen\s?shot|picture|screen\s?grab)"
            r"(?:\s+(?:called|named|of)\s+(?P<name>.+?))?" + END
        ),
        _build_screenshot,
        "Save a picture of the page. Password fields are always masked.",
        ["Take a screenshot", 'Take a screenshot called "after checkout"'],
    ),
]
