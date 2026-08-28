# Connect QA Copilot to your editor and point it at your product

Two steps, about ten minutes. Neither involves writing YAML or a selector.

> **Not a developer?** There is a shorter path that needs no terminal at all:
> install the Claude Desktop extension and fill in a form.
> See **[INSTALL-DESKTOP.md](INSTALL-DESKTOP.md)**.

1. [Point it at your app](#1-point-it-at-your-app) — `qa-copilot init`
2. [Connect it to Claude Code](#2-connect-it-to-claude-code) — one command
   · or [Claude Desktop](#2b-connect-it-to-claude-desktop)

---

## 1. Point it at your app

```bash
cd "/path/to/QA Copilot"
.venv/bin/qa-copilot setup      # fills a form in your browser
```

or, once it is connected to an assistant, just ask it — *"set up QA Copilot for
my app"* — and it will open the same page. Nothing about either route requires
knowing a command; the page signs in once to check the details, then writes the
configuration and the secret itself.

There is also a terminal wizard, if you prefer questions to a form:

```bash
.venv/bin/qa-copilot init
```

It asks a handful of plain questions, **opens a real browser to find your
sign-in form**, and writes the configuration itself:

```
Let's point QA Copilot at your application.
  Nothing is written until the end, and your password is never shown.

  What is the web address of the app you want to test?
  > https://qa.myproduct.internal

  What should we call this environment? [qa]
  > qa

  Where is the sign-in page? [/login]
  > /signin

  Opening a browser to look at https://qa.myproduct.internal/signin …
  Found the sign-in form:
    username field  →  field labelled "Work email"
    password field  →  test id "password-input"
    sign-in button  →  button labelled "Sign in"

  Does that look right? [Y/n] y

  Now a test account to sign in with.
  What should we call it? (a short name, like admin) [admin]
  > admin

  What should this account be able to do?
      browse           look at pages that any signed-in user can see
      manage_users     add, edit or disable other people's accounts
      manage_settings  change application or account settings
      create_order     place an order / submit the main thing this app does
  Which ones? (comma separated) [browse]
  > browse, manage_users

  Username or email for the admin account?
  > qa-admin@myproduct.com
  Password? (not shown, not saved to your shell history)
  > ********

  Checking those details work …
  Signed in. Landed on /home
  (a wrong password shows: testid='login-error')

  Done.
    wrote  config/environments.yaml   (how to sign in)
    wrote  config/identities.yaml     (the ADMIN_USER account)
    wrote  .env                       (the password, kept out of git)
    added  'qa' to the policy allow-list
    wrote  my-first-test.txt          (a test you can run right now)
```

Then:

```bash
.venv/bin/qa-copilot run my-first-test.txt --headed
```

Watch a browser sign in using a password that appears in no file you wrote.

### What it did, and what it deliberately did not do

- **Found your form** by looking at it, preferring `data-testid`, then a label,
  then a placeholder, then a `name` attribute. You never typed a selector.
- **Proved the credentials work** by signing in before writing anything. A wrong
  password stops the wizard and writes nothing.
- **Found your error element too**, by signing in wrongly on purpose — so a bad
  password later fails in a second instead of timing out.
- **Put the password in `.env` only**, `chmod 600`, already in `.gitignore`. The
  config files hold a `secret://` *reference*, never a value. Add more accounts
  by running `init` again.

### If it cannot find the form

It tells you what it *did* find on the page. Common causes:

| Problem | Fix |
|---|---|
| Sign-in is behind a "Log in" button | Give the address of the form itself, not the landing page |
| A cookie banner covers the page | Dismiss it once in a normal browser, or give the direct form URL |
| Username and password are on separate screens | Not supported by the wizard — that recipe needs writing by hand |
| Single sign-on / an identity provider | Point `init` at the IdP's own login page, or use an API token account |

### Adding more accounts and environments

Run `qa-copilot init` again. Each run adds one environment and one account. To
add a second account to an existing environment, copy the block in
`config/identities.yaml`, change the alias and the `secret://` paths, and add the
matching lines to `.env`:

```
secret://qa/viewer/password   →   QA_SECRET__QA__VIEWER__PASSWORD=...
```

---

## 2. Connect it to Claude Code

### The short way: install it from GitHub

```
/plugin marketplace add kesav-aot/qa-copilot
/plugin install qa-copilot@qa-copilot
```

That brings the MCP server, the five skills, the three subagents and the
credential-blocking hook in one step. Nothing needs to be installed first: the
plugin's server command is a launcher that provisions its own Python and
dependencies on first start, under `~/.qa-copilot/runtime`. The first launch
takes a minute; later ones are immediate.

Use this when you want QA Copilot as a tool. Use the manual route below when
you are working *on* QA Copilot, or want it pointed at a checkout you control.

### The manual way

```bash
cd "/path/to/QA Copilot"
.venv/bin/qa-copilot mcp-config
```

That prints a ready-to-run command with **absolute paths**, so it works from any
directory — including your product's own repo, which is where you will actually
be working:

```bash
claude mcp add-json qa-copilot --scope user '{"command":"/path/to/QA Copilot/.venv/bin/qa-copilot-mcp", ...}'
```

Then run `/mcp` inside Claude Code. You should see **qa-copilot** with 17 tools.

### Which scope

| Scope | Use when |
|---|---|
| `--scope user` | You want QA Copilot available in every project. **Start here.** |
| `--scope project` | Your team shares one repo and everyone should get it on clone. Writes `.mcp.json`, which you commit. |
| `--scope local` | Just this project, just you. |

> **Do not use relative paths.** The `.mcp.json` in this repository uses them so
> it works when Claude Code is opened *here*. From your product's repo they
> resolve against the wrong directory and the server will not start. Always use
> what `mcp-config` prints.

### Optional: the full plugin

The MCP server alone gives Claude the tools. Installing the plugin
(`.claude-plugin/plugin.json`) also gives it:

- **five skills** — how to write a test, run one, automate a test case, diagnose
  a failure;
- **three subagents** — designer, executor, failure analyst, each with only the
  tools its job needs;
- **a hook** that blocks a pasted credential before the model ever sees it.

Worth it if a team will use this. The tools alone are fine for trying it out.

---

## 2b. Connect it to Claude Desktop

```bash
cd "/path/to/QA Copilot"
.venv/bin/qa-copilot mcp-config --desktop --install
```

That merges one entry into
`~/Library/Application Support/Claude/claude_desktop_config.json`, keeping
everything already in it and writing a timestamped backup first.

**Then quit Claude Desktop completely and reopen it.** It only reads that file
at startup — closing the window is not enough. On macOS use ⌘Q, or
`Claude → Quit`.

You will know it worked when the tools icon appears in the message box and lists
**qa-copilot**.

To see the settings without writing them: `qa-copilot mcp-config --desktop`.
To replace an entry that is already there: add `--force`.

### If Desktop says "Operation not permitted"

Claude Desktop's log shows:

```
/bin/sh: /Users/you/Documents/…/qa-copilot-mcp: Operation not permitted
Server disconnected.
```

That is macOS, not QA Copilot. Claude Desktop is sandboxed and cannot execute
anything inside a protected folder — **`~/Documents`, `~/Desktop`,
`~/Downloads`, `~/Pictures`, `~/Movies`, `~/Music`**. Claude Code is unaffected,
because it inherits your terminal's permissions.

Two fixes:

1. **Move the project somewhere unprotected** — `~/qa-copilot`, `~/Developer`,
   `~/Projects`. Then rebuild the virtualenv (its paths are baked in) and
   re-point Claude Desktop:

   ```bash
   mv ~/Documents/qa-copilot ~/qa-copilot
   cd ~/qa-copilot
   rm -rf .venv && python3 -m venv .venv
   .venv/bin/pip install -e ".[dev,demo]"
   .venv/bin/qa-copilot mcp-config --desktop --install --force
   ```

2. **Grant Claude Desktop Full Disk Access** — System Settings → Privacy &
   Security → Full Disk Access → add `Claude.app`. This works but is broad: it
   grants read access to your entire disk, not just this project.

The first is narrower and also removes any space in the path, which is a second
hazard when a host launches the server through a shell.

`qa-copilot doctor` warns about both before you hit them.

### Undoing it

```bash
ls ~/Library/Application\ Support/Claude/*.backup-*     # find the backup
```

Copy it back over `claude_desktop_config.json`, or just delete the `qa-copilot`
block from `mcpServers`. Restart Claude Desktop either way.

### Desktop vs Code — which to use

| | Claude Desktop | Claude Code |
|---|---|---|
| Best for | A QA engineer who wants a chat window and nothing else | Someone already working in a terminal or an editor |
| Setup | One command, then restart the app | One command, no restart |
| Skills, subagents, the credential-blocking hook | Not available — tools only | Available with the plugin |
| Sees your test files as files | No | Yes |

For a non-coding QA engineer, Desktop is the better door. The one thing it does
not give you is the harness hook that blocks a pasted password before the model
sees it — on Desktop that protection falls back to the model's own instructions,
plus the compiler, which still refuses a credential written into a test.

## 2c. Connect it to Antigravity

```bash
cd "/path/to/QA Copilot"
.venv/bin/qa-copilot mcp-config --antigravity --install
```

Merges one entry into `~/.gemini/config/mcp_config.json`, keeping everything
already in it and writing a timestamped backup first. Reload with the `…` at the
top of the agent panel → MCP Servers → Manage MCP Servers, or `/mcp` in the CLI.

For a single project, put the same JSON in `.agents/mcp_config.json` inside that
project instead. To see the settings without writing them, drop `--install`.

Antigravity reads the same skills, rules and agents through
`.agents/plugins/qa-copilot/`, which symlinks to the directories the Claude
plugin uses.

## 3. Check it works

In Claude Code, from anywhere:

> **What test accounts do we have and what can they do?**

Expect the aliases and capabilities you set up — never a username or password.

> **Write me a test that an admin can reach the settings page, then show me
> what each line means before running it.**

Expect a plain-English test plus a line-by-line reading. Something like:

```
# Admin can reach settings

Log in as an admin
Go to /settings
Check the page shows "Settings"
```

| You'd write | It will do |
|---|---|
| Log in as an admin | log in as ADMIN_USER (password from the secret store) |
| Go to /settings | go to /settings |
| Check the page shows "Settings" | check the page shows 'Settings' |

> **Run it.**

### Three things to try that should be refused

These are worth testing on day one, because they are the guarantees.

| Ask | What should happen |
|---|---|
| *"The admin password is hunter2, use that."* | The hook blocks the message before Claude sees it. Without the plugin, Claude refuses and asks for an account name. |
| *"Just approve that risky test yourself."* | It cannot. No tool approves a plan — only `qa-copilot approve` in a terminal. |
| *"Rename the test so it isn't flagged as risky."* | Risk is inferred from what the test *does*. Renaming changes nothing. |

If any of those succeeds, that is a bug — tell me.

---

## 4. What your QA team needs to read

Only one thing: **[WRITING-TESTS.md](WRITING-TESTS.md)**. It assumes no coding
knowledge and covers everything they will hit.

Give them these three commands:

```bash
qa-copilot words                 # every phrase you can write
qa-copilot check my-test.txt     # what did it understand? runs nothing
qa-copilot run my-test.txt       # run it
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/mcp` shows no qa-copilot | Not added, or added with relative paths | Re-run `qa-copilot mcp-config` and use exactly what it prints |
| Claude Desktop shows no tools icon | It was not fully restarted | Quit with ⌘Q — not just closing the window — and reopen |
| Desktop connects but every tool errors | The venv or project folder moved | Re-run `mcp-config --desktop --install --force` |
| Desktop tools appear but find no accounts | Editing `.env` after connecting | Restart Claude Desktop; the server reads it at startup |
| `/mcp` shows it but with 7 tools | Stale connection from an older build | Reconnect, or restart Claude Code |
| Server fails to start | The venv moved, or paths changed | Re-run `mcp-config`; it prints the current absolute paths |
| `doctor`: "identity X has no resolvable credentials" | `.env` line missing or misnamed | `secret://qa/admin/password` → `QA_SECRET__QA__ADMIN__PASSWORD` |
| Login times out on your app | `success_url_contains` is wrong | Sign in by hand, copy the URL you land on, edit `config/environments.yaml` |
| Every test needs approval | A destructive word in a step that acts | Expected for real changes. Read-only tests never need it. |
| Tests pass locally, fail in CI | No browser installed | `playwright install chromium` in the CI image |

Deeper checks — including deliberately trying to break the secret boundary — are
in **[MANUAL-TESTING.md](MANUAL-TESTING.md)**.
