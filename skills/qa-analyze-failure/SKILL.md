---
name: qa-analyze-failure
description: Diagnose a failed QA Copilot run from its sanitised report, screenshot, and audit trail, and propose a root cause with a confidence level. Use when a test fails and someone asks why, or asks whether a failure is a real defect or a flaky test.
---

# Analyse a failure

You are working from sanitised evidence only. You cannot see raw headers,
cookies, or credential values, and you should not ask for them — a root cause
that requires reading a token is a root cause the execution layer should be
surfacing as a sanitised field instead. Say so if you hit that wall.

## Evidence to gather

1. The failing report: `failure.step_index`, `failure.detail`, `failure.page`.
2. The screenshot at `failure.screenshot` — read it.
3. `recent_activity` — the audit trail. Look for `auth.result` with
   `authenticated: false`, or `api.request` entries with unexpected statuses
   just before the failure.

## Separate the three failure families

**Application defect.** The app rendered or returned something the requirement
says it should not. The page snapshot supports it. Highest value; state the
requirement it violates.

**Test defect.** The selector moved, the wait was too short, the plan assumed
state it never established, or the assertion was wrong. Propose the corrected
plan fragment.

**Environment or data problem.** Login failed, an API returned 5xx, the test
identity lacks the capability, or a fixture was consumed by an earlier run.
These are not defects; do not file them as such.

## Report format

```
Root cause hypothesis: <one sentence>
Family:                defect | test | environment
Confidence:            <low | medium | high>, and why

Evidence:
  - <step N failed: quoted detail>
  - <what the screenshot shows>
  - <relevant audit entry>

Alternatives considered:
  - <the next most likely cause, and what would distinguish it>

Recommended next step:
  - <a specific action: a fixed plan fragment, a check to run, a defect to file>
```

State confidence honestly. A guess labelled high confidence is worse than a
guess labelled low. If the evidence genuinely does not distinguish two causes,
say which single check would.
