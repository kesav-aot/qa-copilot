"""The user-facing reference for what QA Copilot understands.

Generated from :data:`qa_copilot.plain.grammar.RULES`, so it is always exactly
what the compiler accepts.
"""

from __future__ import annotations

from typing import Any

from qa_copilot.plain.grammar import RULES

_ORDER = [
    "Signing in",
    "Moving around",
    "Clicking",
    "Filling in forms",
    "Checking",
    "Waiting",
    "API",
    "Evidence",
    "Notes",
]

_INTRO = (
    "Write one instruction per line, in ordinary English. Start a test with a "
    "heading. Everything else is a step."
)

_EXAMPLE_FILE = """# Admin can disable a user
Environment: demo
Tags: users, smoke

Log in as an admin
Go to the Users page
Check the page shows "User Management"
Click Disable for Rae Rivera
Check the page shows "disabled"
"""

_RULES_OF_THUMB = [
    "Never write a password. Say \"log in as an admin\" and the real value is "
    "fetched from the secret store at the moment it is needed.",
    "Name things the way they appear on screen. \"Click the Save button\" works "
    "because the page says Save.",
    "If a word appears more than once on the page, QA Copilot stops and asks "
    "rather than guessing. Say \"Click Disable for Rae Rivera\" or \"the first "
    "Disable button\".",
    "Always check something. A test with no Check line can only fail if the app "
    "crashes.",
    "Prefer waiting for something over waiting a number of seconds.",
]


def phrasebook() -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = {}
    for rule in RULES:
        if not rule.examples:
            continue  # rules that exist only to produce a better error
        categories.setdefault(rule.category, []).append(
            {"does": rule.summary, "examples": rule.examples}
        )
    ordered = {c: categories[c] for c in _ORDER if c in categories}
    ordered.update({c: v for c, v in categories.items() if c not in ordered})
    return {
        "how_to_write_a_test": _INTRO,
        "example_file": _EXAMPLE_FILE,
        "rules_of_thumb": _RULES_OF_THUMB,
        "phrases": ordered,
    }


def render_phrasebook() -> str:
    book = phrasebook()
    out = [
        "How to write a test",
        "===================",
        "",
        book["how_to_write_a_test"],
        "",
        "Example file",
        "------------",
        "",
        *(f"  {line}" for line in book["example_file"].rstrip().splitlines()),
        "",
        "Worth knowing",
        "-------------",
        "",
    ]
    for tip in book["rules_of_thumb"]:
        out.append(f"  * {tip}")
    out.append("")
    out.append("Everything you can write")
    out.append("------------------------")
    for category, entries in book["phrases"].items():
        out.append("")
        out.append(f"{category}")
        out.append("-" * len(category))
        for entry in entries:
            out.append(f"  {entry['does']}")
            for example in entry["examples"]:
                out.append(f"      {example}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"
