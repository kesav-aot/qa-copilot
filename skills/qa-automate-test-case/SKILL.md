---
name: qa-automate-test-case
description: Turn existing manual test cases (Markdown, CSV, Excel, Gherkin, Jira exports) into reviewed, runnable Test DSL plans. Use when someone asks to automate a backlog of test cases, convert a spreadsheet or Jira export, or asks "what can we automate?".
---

# Automate a manual test case

The pipeline is: **ingest → triage → draft → resolve TODOs → validate → save**.
The drafter gives you a scaffold. Turning it into a real test is your job, and
the parts you cannot resolve are the human's.

## 1. See what exists

`list_test_cases` — optionally scoped with `source` to one file or subdirectory.

Read the triage before you read the cases. Each one comes back with:

- `automatable` — false means there are blockers; do not draft it.
- `inferred_risk` — medium and above will need human approval to run.
- `suggested_capability` — who the analyser thinks should run it.
- `blockers` — the specific reasons.

## 2. Deal with the unautomatable ones first

Call `analyze_test_case` and report the findings to the human as work *they*
need to do, with the specific question attached. Do not paper over them:

| Blocker | What you say |
|---|---|
| No expected result | "This case has no observable outcome, so any test I write would pass without verifying anything. What should it check?" |
| A literal credential | "This case contains what looks like a real credential. It needs moving into the secret store as a `secret://` reference before I can touch it." Do not repeat the value. |

Warnings are yours to resolve where you can: a vague "enter valid data" usually
has an obvious concrete answer in context — propose one and say you proposed it.

## 3. Draft

`draft_plan_from_test_case`. You get back `plain_english` (show them this —
they can read it, and they cannot read the DSL), the underlying plan, `notes` on
what was mapped, `todos` on what was not, and `step_coverage_percent`.

**Treat every TODO as work, not commentary.** The common ones:

- *"confirm the path '/x' — it was guessed from the wording"* — check it. The
  drafter slugified an English phrase; real applications rarely agree.
- *"replace the 'Foo' target with a data-testid"* — look at the page and use a
  stable selector. A `text` target breaks on the next copy edit.
- *"expected result '…' needs an explicit assertion"* — the source was prose.
  Turn it into an exact string, a testid, a URL fragment, or a status code.
- *"the case does not say who runs it"* — ask, or infer from context and say so.

Low coverage is information, not failure. A 30% draft of a case written as
narrative prose is the drafter being honest.

## 4. Check the actor especially carefully

The analyser reads the actor from the preconditions and knows about negative
tests, but it is heuristic. On a case like "Standard user cannot manage users",
confirm the plan authenticates as the *standard* user. Authenticating as an admin
would make the test pass for the wrong reason, and nothing downstream would
catch it.

## 5. Validate, then save

`validate_test_plan`, resolve anything it reports, then `save_test_plan`.

Saving is not approving. If the plan came back medium or high risk, tell the
human the fingerprint and the `qa-copilot approve` command rather than trying to
run it.

## Reporting back

For a batch, give a table — case id, verdict, coverage, what you changed, what
still needs a human — and then the list of cases you refused to draft and why.
Do not bury a blocker in a paragraph. A QA lead reading your summary should be
able to see in one pass which cases are automated, which need a decision, and
which need the source test case fixed.
