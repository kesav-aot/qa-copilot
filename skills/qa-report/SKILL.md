---
name: qa-report
description: Deliver the HTML test report — every step with a screenshot — after any run, and build combined reports from earlier runs for a day's testing, a regression pass or a release sign-off. Use after every test run without being asked, and whenever someone asks for a report, results, evidence, screenshots, or "what did we test".
---

# Report on a run

Every run already produces two things without anyone asking:

- a **screenshot after each step** (password fields are masked), and
- a **self-contained HTML report** — verdict, each step in plain words, the
  screenshot beside it, and on a failure what went wrong and what the page said.

The report is opened in the person's browser when the run finishes. **Your job
is to make sure they know it is there, every time** — a user who did not ask for
screenshots still gets them, and that is the point.

## After every run — always

The result of `run_plain_test`, `run_test_plan` and `run_test_suite` carries an
`html_report` block: `path`, `link`, `opened_in_browser`, `counts`.

End your reply with the report, in this shape:

> **Report:** it is open in your browser — 6 steps, each with a screenshot.
> Saved at `…/reports/20261112-101502-a1b2c3-admin-can-reach-user-management.html`

Rules:

- Give the `path` exactly as returned. Do not shorten it, and do not invent one.
- If `opened_in_browser` is false, say the report is ready and give the path —
  do not claim it opened.
- If `html_report` has an `error`, say the report could not be written and why.
  The test result itself is still valid; report it as usual.
- A run that was `blocked` or `invalid` on its own has no report: nothing ran.
  Say that rather than pointing at an old one.
- Do not paste the steps into chat as a substitute. Summarise in a few lines —
  verdict, the failing step if any, your read of the cause — and let the report
  carry the detail.

## When someone asks for a report

| They say | You do |
|---|---|
| "Show me the report again" / "where are the screenshots?" | `generate_report` with no arguments — rebuilds and reopens the latest run. |
| "Report for today's testing" / "everything we ran this morning" | `list_reports`, pick the runs in that window, `generate_report(run_ids=[…], title="…")`. |
| "A sign-off report for release 4.2" | Same, with the title they will recognise. Ask which runs if it is not obvious; do not quietly include a failed exploratory run. |
| "Send it to the team" | The file is self-contained — screenshots are inside it — so it can be attached to a ticket, a chat or an email as it is. Give them the path. You cannot send it anywhere yourself. |
| "As a PDF" | Open the report and print it to PDF from the browser; it is laid out to print cleanly. |

`generate_report` runs nothing. If they want fresh results, run the tests.

## Writing the summary that goes with it

Lead with the number a QA lead needs, then the exceptions:

```
7 of 9 passed.

Failed
  • Standard user cannot reach user management — step 4: the page showed the
    Disable button. Looks like a real permissions defect.
Needs approval
  • Admin can disable a user — changes data; someone must run
    qa-copilot approve 3f9a…

Report: open in your browser · saved at <path>
```

- Never call a run a pass unless every test's status is exactly `passed`. A
  blocked test is not a pass, and neither is one that could not run.
- Keep the three failure families apart — application defect, test defect,
  environment problem. Use `qa-analyze-failure` when the cause is not obvious.
- Quote the failing step's detail; do not paraphrase it into something vaguer.

## If they want fewer screenshots

Screenshots after every step are the default because that is what people want
when they look back at a run. It is a setting, in `config/settings.yaml`:

```yaml
evidence:
  screenshots: every_step   # every_step | key_steps | failures_only
report:
  auto: true                # write a report after every run
  open: true                # and open it in the browser
```

Tell them the setting; do not edit configuration yourself.

## What is in a screenshot

Every field a credential was typed into is masked, and so is every password
input on the page. Anything else the page displayed is in the picture. If a
screenshot shows something that should not be shared — a customer's details, a
token printed on screen — say so before they forward the report.
