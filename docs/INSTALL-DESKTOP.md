# Install QA Copilot in Claude Desktop

For a QA engineer with no Python, no terminal and no interest in either.
Everything below happens in Claude Desktop's own settings window.

## 1. Install

Open Claude Desktop, go to **Settings → Extensions**, and drag
**`qa-copilot-<version>.mcpb`** into the window.

Double-clicking the file also works *if* Windows or macOS has associated
`.mcpb` with Claude Desktop. It often has not — on Windows the file frequently
opens nothing at all, or is offered to another program. Dragging it into the
Extensions pane does not depend on that association, so start there.

## 2. Tell it about your app

You have two ways, and neither is a terminal.

**The easy one — ask.** Install the extension, restart Claude Desktop, then say:

> **Set up QA Copilot for my app.**

It opens a page in your browser. Fill in the address of your app and a test
account, press the button, and it signs in once to check the details before
saving anything. Your password goes straight into a local file only you can
read; the assistant is told the account's nickname — `ADMIN_USER` — and what it
is allowed to do, never the credential.

Use the same page whenever you want to add another account or another
environment: just ask again.

**Or fill in the extension's settings**, below, if you would rather do it at
install time.

## 2b. The extension's settings

Claude Desktop shows a form. Only the last two are secret.

| Field | What to put | Example |
|---|---|---|
| Workspace folder | Where your tests and screenshots are kept | `~/QA Copilot` |
| Web address of the app | Your app, including the port | `http://localhost:3000` |
| Name for this environment | A short word you will use in tests | `local` |
| Path to the sign-in page | The bit after the host | `/login` |
| Short name for the test account | Becomes the alias the AI sees | `admin` → `ADMIN_USER` |
| What this account can do | `browse`, `manage_users`, `manage_settings`, `create_order` | `browse, manage_users` |
| Username | A **dedicated test account** | `qa-admin@yourapp.com` |
| Password | Masked; stored by macOS, never sent to the AI | |

You can leave the address blank and add your app later — just ask the assistant to set it up.

## 3. Restart Claude Desktop

Quit it completely — ⌘Q on macOS, not just closing the window — and reopen.

> **Windows:** supported, but not yet tested on a Windows machine. The launcher
> is [`qa-copilot-launch.ps1`](../packaging/launcher/qa-copilot-launch.ps1),
> run through PowerShell 5.1. If it fails, please report what the extension log
> says — that is the fastest way to get it fixed.

**The first launch takes a few minutes.** It downloads a private Python and a
test browser (about 500 MB, once). If Claude Desktop gives up waiting, quit and
reopen: each attempt keeps what it already finished, so it gets there.

You will know it worked when you ask:

> **What test accounts do we have and what can they do?**

and get back an alias and a list of capabilities — never a username, never a
password.

## 4. Write your first test

> **Write me a test that an admin can reach the settings page, show me what
> each line means, then run it.**

Your tests are plain text files in the workspace folder. Open them in any
editor, change them, and ask Claude to run them again — or run them yourself.
**[WRITING-TESTS.md](WRITING-TESTS.md)** is the only document your team needs.

## Where your password actually goes

You type it into Claude Desktop, which puts it in your operating system's
credential store and hands it to the local test runner as an environment
variable. The runner writes it to `.env` inside your workspace, readable only by
you (`chmod 600`), and puts a **reference** — `secret://local/admin/password` —
in the configuration instead.

The model is given the alias and the capability list. There is no tool it can
call to resolve a `secret://`, and every response is scanned on the way out: if
a real credential ever survived, the response is discarded rather than sent.

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Extension installs but no tools appear | Desktop was not fully restarted | Quit with ⌘Q and reopen |
| "could not finish setup" in the logs | The username or password was blank, or the sign-in failed | Re-check both in Settings → Extensions, then restart Desktop |
| Your account is called `LOCAL_ADMIN_USER`, not `ADMIN_USER` | The demo workspace already had an `ADMIN_USER` | Nothing is wrong — use the name shown by *"what accounts do we have?"* |
| Setup cannot find the sign-in form | The address points at a landing page, or a cookie banner covers the form | Give the address of the form itself |
| Sign-in is single sign-on | Not supported by automatic setup | Use `qa-copilot init` in a terminal, or an API token account |
| Environment named `production`, `prod` or `live` | Refused by policy, deliberately | Point it at a test environment |

## Publishing a new version (for whoever ships it)

Three ways, all producing the same file.

**From the GitHub website**, no terminal: Actions -> **release** -> **Run
workflow**. Leave the version blank and it uses whatever
`packaging/mcpb/manifest.json` says. It builds the bundle, checks it, and
attaches it to a release.

**By tagging**, which is the normal route for a real version:

```bash
git tag -a v0.1.4 -m "QA Copilot 0.1.4"
git push origin v0.1.4
```

The tag must match the version in the manifest, or the workflow refuses to
publish — otherwise people download a file whose name says one version and
whose contents say another.

**Locally**, to inspect the bundle without releasing it:

```bash
.venv/bin/python scripts/build_mcpb.py     # -> dist/qa-copilot-<version>.mcpb
.venv/bin/python scripts/check_mcpb.py     # the same gate CI applies
```

The bundle carries source only — about 115 KB. The Python interpreter, the
dependencies and the browser are fetched on the user's machine at first launch
by [`packaging/launcher/`](../packaging/launcher/) — one script per platform,
shared with the Claude Code plugin —
which is written to be re-runnable: every stage leaves a marker, so an
interrupted first start resumes instead of beginning again.
