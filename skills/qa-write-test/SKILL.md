---
name: qa-write-test
description: Write a browser or API test in plain English from a description of what to check. Use whenever someone asks for a test, a check, or "can you test that…" — this is the default way to author tests, because the person who owns the test must be able to read it.
---

# Write a test in plain English

The person you are writing for is a QA engineer, and they may not read code.
**Plain English is the output format, not a convenience.** A test they cannot
read is a test they cannot own, review, or fix at 9am when it breaks.

Write the DSL directly only if they explicitly ask for it.

## Before you write

1. `get_phrasebook` — the exact vocabulary. Do not invent phrasings; a sentence
   that does not parse is worse than a plainer one that does.
2. `list_identities` — who the test can be. Never ask a human for a password,
   and refuse one if offered.
3. `list_environments` — where it runs.

## Write it

```
# What this test proves
Environment: demo
Tags: smoke

Log in as an admin
Go to /users
Check the page shows "User Management"
```

Rules that matter more than they look:

- **Say who, never a password.** Prefer `Log in as someone who can manage
  settings` over an alias — it states *why* that account, and survives renames.
- **Always check something.** A test with no `Check` line passes unless the app
  crashes. If the request did not say what success looks like, ask.
- **Quote exact on-screen wording** in checks: `Check the page shows "Order
  placed"`.
- **Disambiguate up front.** If the thing you are clicking appears once per table
  row, write `Click Disable for Rae Rivera`, not `Click Disable`. Otherwise the
  run stops and asks.
- **Say the kind of thing** — `the Save button`, `the Email field` — when you
  know it. The resolver then only looks at things of that kind, and tells the
  user when the name exists but is the wrong kind.

## Then check it, and show your working

Call `check_plain_test`. It returns, for each line, `you_wrote` and
`i_understood`.

**Show the human those pairs.** That is how they verify the test says what they
meant, and it is the whole reason plain English is worth using. Do not summarise
them away.

Report anything in `problems` before running — a partially understood file is
not run at all.

Flag two things explicitly if present:

- a `notes` entry about approval — the test changes data and a person must run
  `qa-copilot approve <fingerprint>`;
- a warning that the test checks nothing.

## Running it

`run_plain_test`, then report per the `qa-run-test` skill. If a step fails
because an element was not found, the failure detail lists **what is actually on
the page** — quote it, it is nearly always the fastest route to the fix.

## What good looks like

> Here is the test:
>
> ```
> # Standard user cannot reach user management
>
> Log in as a standard user
> Go to /users
> Verify "Access denied" is displayed
> Check I should not see the Disable button
> ```
>
> Line by line, this is what QA Copilot understood:
>
> | You'd write | It will do |
> |---|---|
> | Log in as a standard user | log in as STANDARD_USER (password from the secret store) |
> | Go to /users | go to /users |
> | Verify "Access denied" is displayed | check the page shows "Access denied" |
> | Check I should not see the Disable button | check the Disable button is NOT on the page |
>
> It is read-only, so it does not need approval. Shall I run it?
