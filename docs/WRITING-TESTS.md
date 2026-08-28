# Writing tests

You write tests in ordinary English, one instruction per line. There is no code,
no selectors, and you never write a password.

```
# Admin can disable a user

Log in as an admin
Go to /users
Click Disable for Rae Rivera
Check the page shows "disabled"
```

Save that as `my-test.txt` and run it:

```bash
qa-copilot run my-test.txt
```

```
Admin can disable a user                                    PASSED  (1.2s)
────────────────────────────────────────────────────────────────────────
  ✓  log in as ADMIN_USER
  ✓  go to /users
  ✓  click Disable in the "Rae Rivera" row
  ✓  page shows "disabled"
```

---

## Contents

1. [The shape of a test file](#1-the-shape-of-a-test-file)
2. [Signing in — and why you never type a password](#2-signing-in)
3. [Naming things on the page](#3-naming-things-on-the-page)
4. [Checking — the part that makes it a test](#4-checking)
5. [Check before you run](#5-check-before-you-run)
6. [When it can't find something](#6-when-it-cant-find-something)
7. [Tests that change data](#7-tests-that-change-data)
8. [Working with an AI assistant](#8-working-with-an-ai-assistant)
9. [Turning your existing test cases into these](#9-turning-your-existing-test-cases-into-these)
10. [Every phrase you can use](#10-every-phrase-you-can-use)

---

## 1. The shape of a test file

```
# What this test is called
Environment: demo
Tags: smoke, checkout

Log in as an admin
Go to /orders
Check the page shows "Recent orders"
```

- A line starting with `#` names a test. Put several in one file if you like —
  each `#` starts a new one.
- `Environment:`, `Tags:`, `Description:` are optional and go under the name.
- Everything else is a step, one per line.
- Lines starting with `//` are ignored — use them for your own notes.
- `1.`, `2.`, `-` and `*` at the start of a line are ignored too, so you can
  paste numbered steps straight out of a test case.

Capitalisation and punctuation do not matter. `click the save button` and
`Click the Save button.` are the same.

---

## 2. Signing in

```
Log in as an admin
Log in as a standard user
Log in as ADMIN_USER
Log in as someone who can manage settings
```

**You never write a password.** You name *who* to be, and QA Copilot fetches the
real credentials from the secure store the instant it needs them. The password
does not go in your file, does not go into version control, and is never shown
to the AI assistant.

If you write a password anyway, the test is refused before it runs:

```
✗ line 3: Log in with password ********
    this line looks like it contains a real password or key.
    Credentials must never be written in a test file — anyone who can read
    the file can read the password, and it would end up in the AI's context.
    try: say who instead: "Log in as an admin"
         available test accounts: ADMIN_USER, STANDARD_USER
```

To see who you can log in as:

```bash
qa-copilot identities
```

**Prefer describing what they need to do** — `someone who can manage settings` —
over naming an account. It keeps the test working when accounts get renamed, and
it says *why* that account was chosen.

If the account you need does not exist, ask whoever set QA Copilot up to add it.
You should never need to handle the password yourself.

---

## 3. Naming things on the page

Name things the way they appear on screen.

```
Click the Save button
Click "Add to cart"
Type "blue widget" into the Search box
Select "Premium" from the Plan dropdown
Tick "Remember me"
```

Adding what kind of thing it is — *button*, *link*, *field*, *dropdown*,
*checkbox*, *heading* — makes it more precise, and QA Copilot will then only
look at things of that kind.

### When the same word appears twice

A table with a Disable button on every row has many things called "Disable".
QA Copilot will **stop and ask** rather than guess:

```
✗  click Disable

   "Disable" matches 2 things on this page, so I stopped rather than guess:
     1. "Disable" (button) in the row "Rae Rivera rae@qa.local active Disable"
     2. "Disable" (button) in the row "Kit Osei kit@qa.local active Disable"

   Narrow it down, for example:
     - "the first Disable"
     - "Disable in the <row text> row"
```

So say which one:

```
Click Disable for Rae Rivera
Click "Edit" in the Kit Osei row
Click the second Disable button
```

This is deliberate. A test that quietly clicked the wrong Delete button is far
worse than one that stopped and asked.

### Labels containing "for" or "in"

`for` and `in the … row` are how you point at one row, so a label that contains
one of those words needs quoting:

```
Click "Save for later"          ← quoted: one label
Click Save for later            ← read as: click Save, in the "later" row
```

Quoting is always safe. When in doubt, quote.

---

## 4. Checking

**A test that checks nothing is not a test.** It can only fail if the app
crashes. QA Copilot warns you if you forget.

```
Check the page shows "Order placed"
Verify "Access denied" is displayed
Check the URL contains /dashboard
Check I can see the Save button
Check I should not see the Delete button
Check the status is 403
```

`Check`, `Verify`, `Confirm`, `Expect` and `Make sure` all mean the same thing.
So do these:

```
Check the page shows "Done"
The page should say "Done"
Verify "Done" is displayed
```

**Put the exact wording in quotes.** `Check the page shows "Order placed"` looks
for those characters. Without quotes it is guesswork.

**Checking something is absent** is how you test permissions:

```
Log in as a standard user
Go to /users
Check I should not see the Disable button
Verify "Access denied" is displayed
```

---

## 5. Check before you run

`qa-copilot check` tells you what QA Copilot understood, without touching the
application:

```bash
qa-copilot check my-test.txt
```

```
Admin can reach user management
───────────────────────────────
  line   4  Log in as an admin
           · log in as admin (a test account that can manage users)
  line   5  Go to the Users page
           · go to /users
           ! I worked out '/users' from your wording — I will find out at run
             time whether that page exists.
  line   6  Check the page shows "User Management"
           · check the page shows 'User Management'

  ✓ ready to run
```

Read the `·` lines. That is QA Copilot telling you what it thinks you meant. If
one of them is wrong, the test is wrong — fix the wording now rather than
debugging a strange failure later.

Do this every time before you commit a test.

---

## 6. When it can't find something

This is the most useful error in the tool, so it is worth knowing what it looks
like. It never just says "not found" — it tells you what *is* there:

```
✗  click the Export button

   I could not find "Export" (button) on http://127.0.0.1:8099/users.
     buttons you can use here:
       - "Disable"
     headings you can use here:
       - "User Management"
     links you can use here:
       - "Dashboard"
       - "Users"
       - "Sign out"

   Check the wording matches what is on screen, or take a screenshot first to
   see where you ended up.
```

Usually one of three things is true:

| What you see | What it means |
|---|---|
| The list has what you wanted, spelled differently | Copy the wording from the list |
| The list is from the wrong page | An earlier step went somewhere unexpected — add `Take a screenshot` before it and look |
| It found the right words but the wrong kind of thing | It will say so: *"I did find 'Users' (link) — but you asked for a heading."* Drop the word `heading`, or name the real heading |

`Take a screenshot` at any point, and one is saved automatically whenever a step
fails. Password fields are always blanked out in them.

---

## 7. Tests that change data

Anything that deletes, disables, cancels or removes needs a person to approve it
before it runs. You will see this:

```
Admin can disable a user                                   NEEDS APPROVAL
────────────────────────────────────────────────────────────────────────

  This test was not run because it needs a person to approve it first.
  Reason: it is medium risk (it changes or removes data)

  Ask someone on the team to run:
      qa-copilot approve 8ce194a70f2ca942913a24600050effa
```

`qa-copilot check` tells you this **before** you run, so it is not a surprise.

The approval covers that exact test. Change a single line and it needs approving
again — which is the point: someone agreed to *that* test, not to whatever it
became.

Read-only tests never need approval.

---

## 8. Working with an AI assistant

If QA Copilot is connected to Claude Code (or another assistant), you can ask in
the same English you write tests in:

> *"Write me a test that a standard user can't get to the admin settings page."*

The assistant writes a plain-English test, shows you what each line means, and
you approve it. **Ask it for the plain-English version, not the technical one** —
you should be able to read, edit and re-run everything it produces.

Useful things to ask:

- *"What test accounts do we have and what can they do?"*
- *"Check this test file and tell me what you think it does."*
- *"This test fails at step 3. What's on the page at that point?"*
- *"Turn TC-1042 from our test case spreadsheet into a test."*

**Never paste a password into the chat**, even if asked. QA Copilot blocks it,
and a correctly behaving assistant will refuse it and ask you to use an account
name instead. If an assistant ever asks you for a real password, that is a bug —
report it.

---

## 9. Turning your existing test cases into these

If you already have test cases in Markdown, Excel, CSV, Gherkin `.feature` files
or a Jira export, drop them in the `testcases/` folder:

```bash
qa-copilot cases                       # what have we got?
qa-copilot draft TC-1042 --environment demo --out my-test.txt
```

You get a plain-English test, plus a list of things it could not work out:

```
# Admin can disable an active user
Environment: demo

Log in as someone who can manage users
Go to /users
Click Disable
Check the page shows "disabled"

// Before you rely on this test, please check:
//   - step 1: confirm the page really is at '/users' — I worked that out from your wording
//   - step 2: if 'Disable' appears more than once on the page, say which one
```

**Those comments are work, not decoration.** Fix them, run `qa-copilot check`,
then run it.

Some test cases will be refused outright — one containing a real password, or
one with no expected result (there is nothing to check, so it would pass no
matter what the app did). `qa-copilot analyze TC-1042` explains exactly what to
fix in the original.

---

## 10. Every phrase you can use

```bash
qa-copilot words
```

That prints the complete list, grouped by what you are trying to do, with
examples. It is generated from the tool itself, so it is never out of date.

### The short version

| To do this | Write |
|---|---|
| Sign in | `Log in as an admin` |
| Sign out | `Log out` |
| Go somewhere | `Go to /users` · `Open the Dashboard page` |
| Click | `Click the Save button` · `Click Disable for Rae Rivera` |
| Type | `Type "widget" into the Search box` · `Fill in Email with a@b.com` |
| Choose | `Select "Premium" from the Plan dropdown` |
| Tick a box | `Tick "Remember me"` |
| Wait | `Wait for the page to show "Done"` |
| Check text | `Check the page shows "Done"` |
| Check absence | `Check I should not see the Delete button` |
| Check address | `Check the URL contains /users` |
| Call an API | `Call GET /api/users as an admin, expecting 200` |
| Save evidence | `Take a screenshot called "after checkout"` |
| Leave a note | `Note: covers ticket QA-4417` |

Everything a step can say is in `qa-copilot words`. If a phrase is not in there,
it will not run — and `qa-copilot check` will tell you so before you waste a run.

### Five habits worth having

1. **Never write a password.** Say who, not what.
2. **Always check something.** Otherwise it is not a test.
3. **Quote exact wording.** `"Order placed"`, not `order placed`.
4. **Run `qa-copilot check` first.** Read the `·` lines.
5. **Wait for a thing, not for a number of seconds.** `Wait 3 seconds` works but
   makes tests slow and flaky, and QA Copilot will tell you so.
