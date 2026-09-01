# QA Copilot — plain-English test automation that never sees your passwords

Tests are written in ordinary English by the people who own them. No selectors,
no YAML, no code — and never a password:

```
# Admin can disable a user

Log in as an admin
Go to /users
Click Disable for Rae Rivera
Check the page shows "disabled"
```

```console
$ qa-copilot run my-test.txt

Admin can disable a user                                    PASSED  (1.2s)
────────────────────────────────────────────────────────────────────────
  ✓  log in as ADMIN_USER
  ✓  go to /users
  ✓  click Disable in the "Rae Rivera" row
  ✓  page shows "disabled"
```

An AI assistant can write those tests, and a QA engineer can read, edit and
re-run them without the assistant. **[Start with the writing
guide.](docs/WRITING-TESTS.md)**

## The other half: the model never learns a credential

The model decides *what* to test and *who* to test as. It never learns a single
credential. A trusted local layer resolves aliases into real values microseconds
before typing them into a browser, and scrubs everything on the way back.

```
QA engineer ──▶ AI agent ──▶ Test DSL plan ──▶ policy gate ──▶ secure executor ──▶ app
                (aliases)     (no secrets)     (human approval)  (resolves secrets)
                    ▲                                                   │
                    └──────────── sanitised artifacts ◀─────────────────┘
```

The boundary is code, not instruction. There is no `get_secret` tool to call, no
`approve_plan` tool to self-clear, and every MCP response passes an egress guard
that discards the payload if a known secret survives sanitisation.

## Install it (no terminal, no clone)

If you are a QA engineer, this is the only section you need.

1. Download **`qa-copilot-<version>.mcpb`** from
   [the latest release](https://github.com/kesav-aot/qa-copilot/releases/latest).
2. Open Claude Desktop → **Settings → Extensions** and drag the file in.
   (Double-clicking works only if `.mcpb` is associated with Claude Desktop,
   which on Windows it often is not.)
3. Restart Claude Desktop, then say: **"Set up QA Copilot for my app."**

It opens a page in your own browser, looks at your application's sign-in form,
and asks for exactly the credentials that form wants — a PIN as well, if it has
one. Those go straight into a local file only you can read. The assistant is
told the account's nickname and what it may do, never the credential.

The first launch takes a few minutes while it fetches its own Python and a test
browser. Nothing else is needed: no clone, no `pip`, no virtualenv.

Full instructions, including what to do when something goes wrong:
**[docs/INSTALL-DESKTOP.md](docs/INSTALL-DESKTOP.md)**.

Using Claude Code instead? `/plugin marketplace add kesav-aot/qa-copilot`, then
`/plugin install qa-copilot@qa-copilot`. Same launcher, same setup page.

## Working on QA Copilot itself

Everything below is for developing this tool, not for using it.

## Try it in two commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,demo]" && .venv/bin/playwright install chromium
cp .env.example .env
.venv/bin/python scripts/demo.py
```

`scripts/demo.py` starts a small target app, speaks MCP over stdio exactly as an
AI client would, runs a real Chromium login, hits the approval gate on a
destructive plan, and finishes by grepping every byte the "model" received for
the four demo credentials. That last check is the whole product claim.

## What the model sees

```json
{"identities": [
  {"alias": "ADMIN_USER",
   "description": "Administrator who can manage users and settings.",
   "capabilities": ["browse", "manage_settings", "manage_users"],
   "credentials_configured": true}
]}
```

Aliases, capabilities, and whether the credentials resolve. Not the username, not
the password, not even the `secret://` reference they live behind.

## No selectors, ever

`Click Disable for Rae Rivera` is not compiled to a selector in advance. It is
resolved against the page as it actually is, when the step runs. Two rules govern
the failure path, because that is where a non-coding user is either unblocked or
lost:

**Never silently pick.**

```
✗  click Disable

   "Disable" matches 2 things on this page, so I stopped rather than guess:
     1. "Disable" (button) in the row "Rae Rivera rae@qa.local active Disable"
     2. "Disable" (button) in the row "Kit Osei kit@qa.local active Disable"

   Narrow it down, for example:
     - "the first Disable"
     - "Disable in the <row text> row"
```

**Never just say "not found".**

```
✗  click the Export button

   I could not find "Export" (button) on http://127.0.0.1:8099/users.
     buttons you can use here:   "Disable"
     headings you can use here:  "User Management"
     links you can use here:     "Dashboard", "Users", "Sign out"
```

A test that quietly clicked the wrong Delete button is worse than one that
stopped and asked.

## What the model writes

```yaml
version: 1
name: Admin can reach user management
environment: demo
steps:
  - action: authenticate
    capability: manage_users      # or: identity: ADMIN_USER
  - action: navigate
    path: /users
  - action: assert
    kind: visible
    target: { testid: users-heading }
```

The plan says *who*, never *what credential*. The environment's login recipe
(`config/environments.yaml`) describes how the form is filled; the model does not
choose that, so it cannot be talked into filling a password field somewhere else.

`fill` rejects credential-shaped values at schema-validation time. For a form the
canned login recipe cannot drive, `fill_secret` takes a `secret://` reference —
the plan still carries only the alias.

## The nineteen MCP tools

Testing:

| Tool | Purpose |
|---|---|
| `list_environments` | Names and base URLs. |
| `list_identities` | Aliases, descriptions, capabilities. |
| `list_capabilities` | Every capability any identity holds. |
| `get_test_plan_schema` | The live JSON Schema for the Test DSL. |
| `validate_test_plan` | Schema + policy verdict + risk + fingerprint. No execution. |
| `run_test_plan` | Execute, if policy allows. Returns a sanitised report. |
| `run_test_suite` | Run several saved plans; each passes the same gate. |
| `recent_activity` | Tail of the audit log. |

Getting tests in and out:

| Tool | Purpose |
|---|---|
| `list_test_cases` | Ingest manual test cases and triage each one. |
| `analyze_test_case` | One case in full, with every finding against it. |
| `draft_plan_from_test_case` | Scaffold a plan, with honest TODOs. |
| `save_test_plan` | Put a reviewed plan in the library. |

Plain English:

| Tool | Purpose |
|---|---|
| `get_phrasebook` | Every phrase a test may be written in. |
| `check_plain_test` | Compile English to a plan and read it back. Runs nothing. |
| `run_plain_test` | Compile and run, in one step. |

Connecting an application:

| Tool | Purpose |
|---|---|
| `open_setup` | Open a setup page in the user's own browser. Takes no arguments, so no credential can be passed through it. |
| `setup_status` | Whether that page has been filled in. Returns an alias, never a credential. |
| `list_test_plans` | The library and the defined suites. |
| `get_test_plan` | One saved plan, to edit and save back. |

Deliberately absent: anything that reads a secret, an env var, a file, or the
config; anything that evaluates JavaScript; and anything that approves a plan.

## The approval gate

The policy engine infers risk from the plan itself and takes the higher of that
and the author's declared risk — so a plan cannot be marked `risk: low` into
running. Destructive verbs, `DELETE` calls and `fill_secret` all raise it.

Medium and above needs a human:

```bash
$ .venv/bin/qa-copilot validate examples/disable-user.yaml
  "risk": "medium", "requires_approval": true, "can_execute": false
  "next_step": "a human must approve this plan: `qa-copilot approve 8ce194a70f2ca942…`"

$ .venv/bin/qa-copilot approve 8ce194a70f2ca942913a24600050effa --note "reviewed"
```

The fingerprint is a hash of the canonical plan, so approving one plan approves
exactly that plan. Change a selector and the approval no longer applies.

## From a manual test case to a running test

`testcases/` holds manual cases in Markdown, CSV/TSV, Excel, Gherkin, Jira JSON
exports and plain text. Ingestion normalises them, then triages each one:

```bash
$ qa-copilot cases
ID       FORMAT    STEPS RISK    AUTO  TITLE
TC-1002  markdown      3 medium  yes   Admin can disable an active user
TC-9001  markdown      3 low     NO    BAD EXAMPLE — a test case that must not be ...
         BLOCKER: the case states no expected result, so any generated test would assert nothing
         BLOCKER: the test case appears to contain a literal credential
```

Drafting produces a scaffold, never a finished test — it maps what it can read
with confidence and says so about the rest:

```bash
$ qa-copilot draft TC-1003 --environment demo
steps:
- action: authenticate
  capability: browse
- action: navigate
  path: /users
- action: assert
  kind: text
  expected: Access denied

# coverage: 100% of steps mapped
# TODO: step 1: confirm the path '/users' — it was guessed from the wording
# TODO: expected result 'Access is refused' needs an explicit assertion
```

That case is titled "Standard user cannot **manage users**". A naive reader picks
up "manage users" and authenticates as an admin — and the negative test then
passes for entirely the wrong reason. The analyser reads the actor from the
preconditions, notices the case is negative, and picks `browse` instead.

Drafting is **refused outright** for a case containing a literal credential, so
the value never gets copied into a plan, a file, or the conversation.

Reviewed plans go into `plans/` and group into suites:

```bash
qa-copilot draft TC-1003 --environment demo --save
qa-copilot suite authz
```

A suite runs every plan through the same policy gate, so an unapproved
destructive plan comes back `blocked` while the rest still run — and the suite
reports `failed`, never a pass.

## Setup for a real application

```bash
qa-copilot init          # finds your login form in a browser, writes the config
qa-copilot mcp-config                      # connect to Claude Code
qa-copilot mcp-config --desktop --install  # connect to Claude Desktop
```

`init` asks plain questions, opens a real browser to locate the sign-in fields,
takes the password without echoing it, proves it works by signing in, and writes
`config/` and `.env` itself. See **[docs/CONNECT.md](docs/CONNECT.md)**.

<details>
<summary>Or configure it by hand</summary>

1. **Store the secrets.** `secret://demo/admin/password` → env var
   `QA_SECRET__DEMO__ADMIN__PASSWORD`. Switch `config/settings.yaml` to the
   `file` provider for a Fernet-encrypted local vault, or add a Vault/AWS/GCP
   backend in `qa_copilot/secrets/` — it is one `SecretProvider` subclass.
2. **Describe the environment** in `config/environments.yaml`, including the
   login recipe. This is the only place login mechanics are written down.
3. **Describe the identities** in `config/identities.yaml` — capabilities and
   `secret://` references.
4. **Check the wiring:** `.venv/bin/qa-copilot doctor`.
5. **Connect the agent.** Claude Code picks up `.mcp.json`, or install the
   plugin (`.claude-plugin/plugin.json` bundles the MCP server, three skills,
   three subagents, and the credential-guard hook). Antigravity reads the same
   core through `.agents/plugins/qa-copilot/`.

Keep `production` in `policy.blocked_environments`. The engine refuses it before
anything launches.

</details>

## CLI

```bash
qa-copilot doctor                       # config, secrets and Playwright wiring
qa-copilot identities --environment demo
qa-copilot schema                       # the Test DSL JSON Schema
qa-copilot validate examples/disable-user.yaml
qa-copilot run my-test.txt [--headed]    # plain English or a DSL plan
qa-copilot check my-test.txt             # what did it understand? runs nothing
qa-copilot words                         # every phrase you can write
qa-copilot approve <fingerprint> [--note "..."]
qa-copilot revoke <fingerprint>
qa-copilot audit --limit 30

qa-copilot cases                        # ingest and triage manual test cases
qa-copilot analyze TC-1002              # one case, with every finding
qa-copilot draft TC-1002 --environment demo [--out f.yaml] [--save]
qa-copilot plans                        # the library and the suites
qa-copilot suite authz [--stop-on-failure]
```

## Layout

```
qa_copilot/
  plain/        plain English → DSL: grammar, compiler, phrasebook, writer
  report.py     results rendered for a person, not a machine
  dsl/          Test DSL — pydantic schema, the stable AI↔execution contract
  ingest/       manual test cases → normalised cases → analysis → draft plans
  library.py    reviewed plans on disk, and named suites
  secrets/      SecretValue + pluggable providers (env, encrypted file)
  identity/     alias/capability → credentials, least-privilege selection
  policy/       risk inference, environment rules, approval store
  executor/     Playwright session, API client, plan runner  ← only code that reveals secrets
                resolver.py — finds an element from an English phrase, at run time
  sanitize/     exact-value + pattern redaction, the egress tripwire
  audit/        append-only scrubbed JSONL
  engine.py     wiring; every model-visible return value leaves through scrub()
  mcp_server.py the seven tools
  cli.py        the human half — approval lives here and nowhere else

config/         environments, identities, policy, suites
english-tests/  sample plain-English tests
testcases/      sample manual test cases in every supported format
plans/          the plan library
examples/       three runnable plans
demo_app/       throwaway target application
skills/ agents/ rules/ hooks/   Claude plugin content, shared with Antigravity
docs/           threat model
```

## Tests

```bash
.venv/bin/python -m pytest -q      # 399 tests
```

The suite asserts the security properties directly, not just the happy path:
`SecretValue.reveal()` raises outside the executor; identity views contain no
`secret://`; a wrong password fails closed without echoing itself; the audit log
records the alias and never the value; the egress guard discards a payload when
the sanitiser is sabotaged; no forbidden tool name exists on the MCP surface;
ingestion refuses a path outside its directory; a plan name cannot become a
path traversal; and a test case containing a credential is never drafted.

## Docs

- **[docs/CONNECT.md](docs/CONNECT.md)** — point it at your own application and
  wire it into Claude Code. Ten minutes, no YAML.
- **[docs/WRITING-TESTS.md](docs/WRITING-TESTS.md)** — for the QA engineer who
  will write the tests. No jargon, no code. Start here if that is you.
- **[docs/MANUAL-TESTING.md](docs/MANUAL-TESTING.md)** — verify all of this
  yourself by hand, including a section on deliberately trying to break the
  secret boundary.
- **[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md)** — where the claim holds, where
  it is defence in depth, and where it does not hold at all.

## Status

Built: plain-English tests with runtime element resolution, secure login, the
policy gate, sanitised reporting, test-case ingestion from six formats, drafting
with gap analysis, the plan library, and suites.

Not yet built: the test-data broker (creating and reserving fixtures on demand),
non-Playwright adapters, and live Jira/TestRail/Zephyr connectors — ingestion is
file-based for now.
