---
name: qa-project-skill
description: Create or update a skill for the team's own application, so QA Copilot knows how that product works in every future conversation — its pages, roles, wording, the steps that are always needed. Use when someone says "remember how our app works", "teach it our product", "make a skill for <project>", or keeps having to explain the same thing about their application.
---

# Write a skill for this team's product

The built-in skills know how to test *a* web application. A project skill is
how a team teaches QA Copilot about *theirs*. It is a Markdown file in the
workspace's `project-skills` folder; once saved it is listed by `list_skills` in every
conversation, and a project skill replaces a built-in one of the same name.

`qa-formsflow` is a worked example of the kind of thing to write — read it if
you want to see the level of detail that is useful.

## 1. Find out what is worth writing down

Only write what the model would otherwise get wrong or have to ask. Ask the
person, and where you can, look — run a short read-only test against the app
and read the report rather than guessing.

- **What the product is**, in two sentences, and the words it uses for things
  (is it an "application", a "submission" or a "case"?).
- **Who signs in** — by alias and capability from `list_identities`, and what
  each role can and cannot see. Never a username or password.
- **Where things are** — the real addresses of the main pages. Guessed paths
  are the commonest reason a first test fails.
- **How its controls behave** — anything that is not a plain button or field:
  drag-and-drop, a rich-text editor, a custom dropdown, a multi-step wizard, a
  page inside an iframe, something that opens in a new tab.
- **What "done" looks like** — the exact confirmation wording after saving,
  submitting, publishing. Tests check against these.
- **What is slow** — pages that need a wait, and what to wait *for*.
- **What must never be touched** — data or environments that tests keep away
  from.
- **The checks this team always wants** — their smoke test, their definition
  of a regression pass.

## 2. Write it

Keep it under a few hundred lines. Shape:

```markdown
# Testing <product>

<What it is, in two sentences. The vocabulary.>

## Signing in
<Which alias or capability for which kind of test.>

## Where things are
| Page | Address | Who can open it |

## How to do the common things
<Plain-English lines that are known to work, copied from tests that passed.>

## Things that catch people out
<The custom dropdown, the drag-and-drop, the slow page.>

## What success looks like
<Exact on-screen wording to check for.>

## Do not
<What tests must leave alone.>
```

Rules:

- **Working lines beat description.** `Click "Save & Publish"` is worth more
  than "there is a save button".
- **Exact wording, in quotes.** Checks match what is on screen.
- **Say why** for anything surprising, so it can be judged when the product
  changes.
- **No credentials, ever.** Refer to accounts by alias. A skill containing
  anything that looks like a password or token is refused.
- **Only what you verified or were told.** Mark anything you are unsure of as
  "unconfirmed" rather than stating it.

## 3. Show it, then save it

Show the person the full text and ask if it is right. Then:

`save_project_skill(name, description, content)`

- `name` — lower-case words joined by hyphens: `acme-claims-portal`.
- `description` — one sentence saying **when to use it**, with the words
  someone would actually say ("…use when testing the Acme claims portal, claim
  submission, or adjuster review"). The description is how the skill is found.
- `content` — the Markdown above.

Tell them where it was saved. It is an ordinary file: they can open it and edit
it by hand, and copy the folder to a colleague's workspace to share it.

## 4. Keep it true

When a test fails because the skill was wrong — a page moved, a label changed
— fix the skill as well as the test: load it with `get_skill`, correct it, show
the change, and save it under the same name. A skill that is out of date is
worse than none, because it is believed.
