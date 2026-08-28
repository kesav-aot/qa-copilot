#!/usr/bin/env python3
"""Hook: refuse to let a raw credential enter the conversation.

The security model does not rely on the model behaving well, so this runs in the
harness rather than in a prompt. On UserPromptSubmit it inspects the text a human
is about to send; on PreToolUse it inspects the tool input. If either looks like
a live credential, the call is blocked with an explanation of the alias workflow.

Exit codes: 0 allow, 2 block (stderr is shown to the model).
"""

from __future__ import annotations

import json
import re
import sys

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("a JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")),
    ("an Authorization header", re.compile(r"(?i)\bauthorization\s*:\s*(bearer|basic)\s+\S{8,}")),
    ("an API key", re.compile(r"\b(sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b")),
    ("an AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a connection string with an inline password",
     re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]{4,}@[^\s]+")),
    ("a password assignment",
     re.compile(r"(?i)\b(password|passwd|pwd)\b\s*(is|=|:)\s*[\"']?\S{6,}")),
]

GUIDANCE = (
    "QA Copilot blocked this because it appears to contain {what}.\n"
    "Credentials must never enter the model's context.\n\n"
    "Instead:\n"
    "  1. Store the value:  QA_SECRET__<ENV>__<ACCOUNT>__PASSWORD=... in .env\n"
    "     (or your secret manager), giving it a secret:// reference.\n"
    "  2. Add or update the identity in config/identities.yaml with that reference.\n"
    "  3. Refer to the identity by alias or capability in the test plan.\n\n"
    "If this was a false positive — a placeholder or an example — rephrase it "
    "without the literal value."
)


def scan(text: str) -> str | None:
    for what, pattern in PATTERNS:
        if pattern.search(text):
            return what
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    event = payload.get("hook_event_name", "")
    if event == "UserPromptSubmit":
        text = payload.get("prompt", "")
    else:
        text = json.dumps(payload.get("tool_input", {}))

    what = scan(text)
    if what:
        print(GUIDANCE.format(what=what), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
