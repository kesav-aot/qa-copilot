"""Analyse a manual test case for the things that stop it becoming a good
automated test.

This runs *before* the model drafts anything, and deliberately in plain code:
these are the checks you want to give the same answer every time. The model's
job is to resolve what this surfaces, not to decide whether it matters.
"""

from __future__ import annotations

import re

from qa_copilot.identity.broker import IdentityBroker
from qa_copilot.ingest.models import Analysis, Finding, ManualTestCase

# --- credential-shaped content --------------------------------------------
_CREDENTIAL_PATTERNS = [
    # "password: Hunter2Example", "password = Hunter2Example", "password Hunter2Example".
    # The value must mix letters and digits, so "password field" does not trip it.
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|passphrase)\b\s*(?:is|=|:)?\s+[\"']?"
        r"((?=\S*\d)(?=\S*[A-Za-z])\S{6,})"
    ),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\b\s*(?:is|=|:)\s+[\"']?(\S{8,})"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),
    re.compile(r"\b(sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# --- vagueness -------------------------------------------------------------
_VAGUE_TERMS = {
    "valid": "which value counts as valid?",
    "invalid": "which value counts as invalid?",
    "appropriate": "appropriate by what rule?",
    "correct": "correct compared to what?",
    "properly": "what specifically must be true?",
    "as expected": "state the expectation explicitly",
    "as needed": "state the condition explicitly",
    "should work": "name the observable outcome",
    "etc": "enumerate the remaining cases",
    "and so on": "enumerate the remaining cases",
    "some": "which one?",
    "various": "enumerate them",
    "relevant": "relevant by what rule?",
}

_CONDITIONAL = re.compile(r"(?i)\b(if|when|otherwise|either|depending on)\b")

# --- data dependencies -----------------------------------------------------
_DATA_PHRASES = [
    (re.compile(r"(?i)\ban? existing\b"), "needs a pre-existing record"),
    (re.compile(r"(?i)\ban? active\b"), "needs a record in a specific state"),
    (re.compile(r"(?i)\bexpired\b"), "needs a record in a specific state"),
    (re.compile(r"(?i)\bpreviously (created|placed|ordered|registered)\b"), "needs prior state"),
    (re.compile(r"(?i)\btest data\b"), "unspecified test data"),
]

_EXTERNAL = [
    (re.compile(r"(?i)\b(e-?mail|inbox|mailbox)\b"), "email"),
    (re.compile(r"(?i)\b(sms|text message|otp|one[- ]time (code|password))\b"), "SMS/OTP"),
    (re.compile(r"(?i)\b(captcha|recaptcha)\b"), "CAPTCHA"),
    (re.compile(r"(?i)\b(payment gateway|3ds|3-d secure|stripe|paypal)\b"), "payment provider"),
    (re.compile(r"(?i)\b(pdf|download the file|printed)\b"), "file/PDF inspection"),
]

_DESTRUCTIVE = re.compile(
    r"(?i)\b(delete|remove|disable|deactivate|purge|drop|revoke|cancel|archive|reset)\b"
)

# --- who runs the test -----------------------------------------------------
#
# Two tiers, and the distinction matters. An ACTOR hint names the role outright
# ("logged in as an administrator"). An ACTION hint only implies a role from
# what the test does ("manage users"). Actor hints always win, and action hints
# are ignored entirely on a negative test — "Standard user cannot manage users"
# is about the standard user, not about an admin.

_ACTOR_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?i)\badmin(istrator)?\b"), ["manage_users", "manage_settings", "admin"]),
    (re.compile(r"(?i)\bsuper ?user\b"), ["manage_settings", "admin"]),
    (re.compile(r"(?i)\b(standard|normal|regular|ordinary|basic) user\b"), ["browse"]),
    (re.compile(r"(?i)\bread[- ]only|viewer\b"), ["browse"]),
    (re.compile(r"(?i)\b(customer|shopper|buyer)\b"), ["create_order", "browse"]),
]

_ACTION_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?i)\bmanage (users|accounts)\b"), ["manage_users"]),
    (re.compile(r"(?i)\b(settings|configuration)\b"), ["manage_settings"]),
    (re.compile(r"(?i)\b(order|checkout|cart|purchase)\b"), ["create_order"]),
]

# A sentence is in "actor position" if it is a precondition, or mentions signing
# in. Searching those first stops "open the admin settings page" from being read
# as "the actor is an admin".
_ACTOR_CONTEXT = re.compile(r"(?i)\b(log(?:ged)? ?in|log ?on|sign(?:ed)? ?in|authenticated|as an? )\b")

_NEGATIVE = re.compile(
    r"(?i)\b(cannot|can ?not|can'?t|should not|shouldn'?t|must not|is not able|unable to|"
    r"denied|refused|forbidden|unauthori[sz]ed|no access|without permission|blocked from|"
    r"prevented from)\b"
)

_AUTH_HINT = re.compile(r"(?i)\b(log ?in|log ?on|sign ?in|authenticate|logged in)\b")


def _add(findings: list[Finding], **kwargs) -> None:
    findings.append(Finding(**kwargs))


def analyze(
    case: ManualTestCase,
    broker: IdentityBroker | None = None,
    environment: str | None = None,
) -> Analysis:
    findings: list[Finding] = []
    blob = case.all_text()

    # --- structural blockers ---------------------------------------------
    if not case.steps:
        _add(
            findings,
            kind="gap",
            severity="blocker",
            message="the case has no steps, so there is nothing to automate",
            suggestion="add numbered steps, or point the parser at the right column/section",
        )
    if not case.expected_results and not any(s.expected for s in case.steps):
        _add(
            findings,
            kind="gap",
            severity="blocker",
            message="the case states no expected result, so any generated test would assert nothing",
            suggestion="add an observable outcome — a page, a message, a status code",
        )

    # --- credentials in the source ---------------------------------------
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(blob):
            _add(
                findings,
                kind="security",
                severity="blocker",
                message="the test case appears to contain a literal credential",
                suggestion=(
                    "move it into the secret store as a secret:// reference and refer to "
                    "the identity by alias; the value must not reach the model"
                ),
            )
            break

    # --- vagueness ---------------------------------------------------------
    for term, question in _VAGUE_TERMS.items():
        for step in case.steps:
            if re.search(rf"(?i)(?<![.\-/@\w])\b{re.escape(term)}\b(?![.\-/@])", step.text()):
                _add(
                    findings,
                    kind="ambiguity",
                    severity="warning",
                    message=f"step {step.number} says {term!r}",
                    location=f"step {step.number}",
                    suggestion=question,
                )
                break

    for step in case.steps:
        if _CONDITIONAL.search(step.action):
            _add(
                findings,
                kind="ambiguity",
                severity="warning",
                message=f"step {step.number} is conditional, so it describes more than one path",
                location=f"step {step.number}",
                suggestion="split it into separate test cases, one per branch",
            )

    # --- data dependencies -------------------------------------------------
    for pattern, why in _DATA_PHRASES:
        if pattern.search(blob):
            _add(
                findings,
                kind="data",
                severity="warning",
                message=f"the case {why}",
                suggestion=(
                    "state how that record is created or selected; QA Copilot has no "
                    "test-data broker yet, so a human must guarantee it exists"
                ),
            )

    # --- things a browser cannot reach ------------------------------------
    external: list[str] = []
    for pattern, what in _EXTERNAL:
        if pattern.search(blob):
            external.append(what)
    for what in external:
        _add(
            findings,
            kind="gap",
            severity="warning",
            message=f"the case depends on {what}, which the browser executor cannot observe",
            suggestion="verify it through an API step, or split that assertion out as manual",
        )

    # --- authentication ----------------------------------------------------
    if not _AUTH_HINT.search(blob):
        _add(
            findings,
            kind="ambiguity",
            severity="info",
            message="no authentication step is described",
            suggestion="confirm whether this test runs signed out, or add the actor",
        )

    # --- risk --------------------------------------------------------------
    inferred = "low"
    if _DESTRUCTIVE.search(blob):
        inferred = "medium"
        _add(
            findings,
            kind="risk",
            severity="info",
            message="the case performs a destructive action",
            suggestion="the policy engine will require human approval before it runs",
        )

    # --- who should run it -------------------------------------------------
    negative = bool(_NEGATIVE.search(blob))

    actor_lines = list(case.preconditions)
    for line in [case.title, *(s.text() for s in case.steps)]:
        if _ACTOR_CONTEXT.search(line):
            actor_lines.append(line)
    actor_text = "\n".join(actor_lines)

    wanted: list[str] = []
    for haystack in (actor_text, blob):
        for pattern, capabilities in _ACTOR_HINTS:
            if pattern.search(haystack):
                wanted.extend(capabilities)
        if wanted:
            break

    if not wanted and not negative:
        for pattern, capabilities in _ACTION_HINTS:
            if pattern.search(blob):
                wanted.extend(capabilities)

    if negative:
        _add(
            findings,
            kind="ambiguity",
            severity="info",
            message="this reads as a negative test — it asserts something is refused",
            suggestion=(
                "confirm the actor is the one being denied, not the one who holds the "
                "permission the case mentions"
            ),
        )

    suggested_capability = None
    suggested_identity = None
    if broker is not None:
        available = set(broker.list_capabilities())
        suggested_capability = next((c for c in wanted if c in available), None)
        if suggested_capability is None and wanted:
            _add(
                findings,
                kind="gap",
                severity="warning",
                message=(
                    "the case implies a role "
                    f"({', '.join(sorted(set(wanted))[:3])}) that no configured identity has"
                ),
                suggestion=(
                    "add the capability to an identity in config/identities.yaml, or pick "
                    "from: " + (", ".join(sorted(available)) or "<none configured>")
                ),
            )
        elif suggested_capability is None:
            _add(
                findings,
                kind="ambiguity",
                severity="warning",
                message="the case does not say who performs it",
                suggestion="choose an identity: " + (", ".join(sorted(available)) or "<none>"),
            )
        if suggested_capability and environment:
            try:
                # Reported for review only; the draft plan keeps the capability,
                # so it stays portable across environments.
                suggested_identity = broker.select(suggested_capability, environment).alias
            except Exception:
                suggested_identity = None
    elif wanted:
        suggested_capability = wanted[0]

    blockers = [f for f in findings if f.severity == "blocker"]
    return Analysis(
        case_id=case.id,
        findings=findings,
        suggested_capability=suggested_capability,
        suggested_identity=suggested_identity,
        inferred_risk=inferred,  # type: ignore[arg-type]
        automatable=not blockers,
    )
