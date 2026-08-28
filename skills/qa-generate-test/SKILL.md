---
name: qa-generate-test
description: Turn a prompt, requirement, or an existing manual test case into a QA Copilot Test DSL plan that references identity aliases instead of credentials. Use when someone asks to automate a test, convert a test case from Jira/TestRail/CSV/Gherkin, or write a new browser or API test.
---

# Generate a test plan

> **Reach for `qa-write-test` first.** Plain English is the default output,
> because the QA engineer who owns the test has to be able to read it. Use this
> skill when someone explicitly wants the DSL, or when a test needs something
> plain English cannot express (a `fill_secret` step, a hand-tuned selector).

You produce a **Test DSL plan**, not Playwright code. The plan is reviewed by a
human and executed by a trusted runtime that holds the credentials.

## Before writing anything

1. `get_test_plan_schema` — author against the live schema, not from memory.
2. `list_environments` — pick the target; never invent one.
3. `list_identities` / `list_capabilities` — find who the test needs to be.

## Rules you cannot bend

Read `rules/security.md`. In short: no credential ever appears in a plan. An
`authenticate` step takes an alias or a capability, and the runtime does the rest.

If the source test case says *"Login with a valid admin username and password"*,
that becomes:

```yaml
- action: authenticate
  capability: manage_users
```

Do **not** ask which admin account, and do **not** add `fill` steps for the login
form — the environment's login recipe already describes it.

## Converting a manual test case

Work through it in this order, and say what you did at each stage:

1. **Actor** → an identity alias or capability.
2. **Preconditions** → either steps at the top of the plan, or a note to the human
   if the state cannot be established through the UI or API.
3. **Steps** → DSL actions with stable targets: prefer `testid`, then
   `role` + `name`, then `label`. Use `css` only as a last resort and say why.
4. **Expected results** → `assert` steps. A plan with no assertions can pass
   without verifying anything; the validator warns about this and you should not
   ship one.
5. **Gaps** → list every ambiguity you had to resolve and every assumption you
   made. This is the part the QA engineer reviews most closely.

## Finish by validating

Call `validate_test_plan`. Report back:

- the policy verdict and inferred risk,
- the fingerprint and, if approval is required, the exact command the human runs,
- any unresolved identities,
- your list of assumptions and gaps.

Do not call `run_test_plan` in this skill. Generating and running are separate
decisions, and the human makes the second one.
