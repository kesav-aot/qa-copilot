---
name: qa-run-test
description: Validate, gate, and execute a QA Copilot Test DSL plan against a configured environment, then report the sanitised result. Use when someone asks to run, execute, or re-run a test plan.
---

# Run a test plan

## Sequence

1. `validate_test_plan` first, always. Running an unvalidated plan wastes a
   browser launch on a schema error.
2. Read the policy verdict:
   - `can_execute: true` → proceed.
   - `requires_approval` and not approved → **stop**. Show the human the
     fingerprint and the command `qa-copilot approve <fingerprint>`. Do not
     rewrite the plan to dodge the gate; that is a policy violation, not a fix.
   - `allowed: false` → report the violations. These are not negotiable.
3. `run_test_plan`. Use `headless: true` unless a human asked to watch.

## Reading the report

The report is already sanitised. It gives you per-step status, durations,
artifact paths, and on failure a screenshot plus a scrubbed page snapshot.

Summarise for a human, in this shape:

- **Verdict** — passed / failed / error / blocked, and at which step.
- **What actually happened** — quote the failing step's `detail`, not the whole trace.
- **Evidence** — the screenshot path and the relevant part of the page snapshot.
- **Next action** — a defect, a flaky selector, a stale test, or an environment problem.

Distinguish these carefully:

| Report shape | Usually means |
|---|---|
| `status: failed` on an `assert` | The application did not do what the test expected — a candidate defect. |
| `status: error` on a `click`/`fill` timeout | The selector or the page state, more often than the application. |
| `authentication as X failed` | Test identity, not the feature under test. Check with the human before filing anything. |
| `status: blocked` | Policy. Nothing ran. |

Never claim a test passed unless `status` is exactly `passed`.

## If something looks like a leak

If any field in the report looks like a real credential, or the response is a
`SECURITY_VIOLATION`, stop and tell the human immediately. Do not repeat the
value back in your summary.
