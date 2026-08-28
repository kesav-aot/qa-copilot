"""A local setup page, so nobody has to open a terminal — or type a credential
into a chat window.

Started by the ``open_setup`` MCP tool or ``qa-copilot setup``. It binds to
127.0.0.1 on a port the operating system picks, behind a one-time token, and
serves a single form. On submit it runs the same wizard as ``qa-copilot init``:
a real browser looks at the sign-in page, finds the fields, signs in to prove
the details work, and only then writes the configuration.

The password travels from the browser on this machine into the secret store
inside this process. It is not returned by any endpoint, not written to the
status, and not logged. The model is given a URL and, afterwards, an alias — it
has no way to read the value back.

Deliberately built on http.server: this must not add a dependency, and it must
not be reachable from anywhere but this machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import io
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

CAPABILITIES = [
    ("browse", "look at pages any signed-in user can see"),
    ("manage_users", "add, edit or disable other people's accounts"),
    ("manage_settings", "change application or account settings"),
    ("create_order", "place an order, or submit the main thing this app does"),
]

# Long enough that someone can go and find the test account's password, short
# enough that a forgotten tab is not left listening all day.
IDLE_TIMEOUT_SECONDS = 30 * 60


class SetupSession:
    """State for one run of the setup page. Holds no credential after use."""

    def __init__(self, config_dir: Path, secrets_file: Path, work_dir: Path) -> None:
        self.config_dir = config_dir
        self.secrets_file = secrets_file
        self.work_dir = work_dir
        self.token = secrets.token_urlsafe(24)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.status: dict[str, Any] = {"state": "waiting", "detail": "Nothing submitted yet."}
        self.httpd: ThreadingHTTPServer | None = None

    # -- status ------------------------------------------------------------
    def set_status(self, **fields: Any) -> None:
        with self.lock:
            self.status = dict(fields)

    def public_status(self) -> dict[str, Any]:
        """What may leave this process. Never contains a credential: the fields
        are fixed here rather than copied from anything the browser sent."""
        with self.lock:
            s = dict(self.status)
        return {
            "state": s.get("state", "waiting"),
            "detail": s.get("detail", ""),
            "environment": s.get("environment"),
            "alias": s.get("alias"),
            "url": s.get("url"),
        }

    @property
    def url(self) -> str:
        assert self.httpd is not None
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/?t={self.token}"

    def expired(self) -> bool:
        return (time.monotonic() - self.started) > IDLE_TIMEOUT_SECONDS

    # -- the work ----------------------------------------------------------
    def fill_existing(self, form: dict[str, str]) -> dict[str, Any]:
        """Add the missing credentials for an identity that already exists."""
        import os

        from qa_copilot.config import load_config
        from qa_copilot.secrets.env import ref_to_env_var
        from qa_copilot.setup.writer import append_secrets

        alias = (form.get("alias") or "").strip()
        try:
            config = load_config(self.config_dir)
            identity = config.identities[alias]
        except Exception:
            return {"state": "failed", "detail": f"No account called {alias!r}."}

        refs = {"username": identity.username_ref, "password": identity.password_ref}
        refs.update(identity.extra_refs)
        values: dict[str, str] = {}
        for name in refs:
            value = form.get(f"field_{name}") or ""
            if not value:
                return {"state": "failed", "detail": f"The {name} is needed."}
            values[name] = value

        environment = identity.environments[0] if identity.environments else None
        if environment:
            problem = asyncio.run(_verify_login(self.config_dir, environment, alias, values))
            if problem:
                return {
                    "state": "failed",
                    "detail": f"Those details did not sign in. Nothing was saved.\n\n{problem}",
                }

        written = append_secrets(
            self.secrets_file, {ref_to_env_var(refs[n]): v for n, v in values.items()}
        )
        # The provider reads os.environ at resolve time, so setting these makes
        # the account usable in the running assistant without a restart.
        for name, value in values.items():
            os.environ[ref_to_env_var(refs[name])] = value
        del values

        return {
            "state": "done",
            "environment": environment,
            "alias": alias,
            "detail": f"Saved {len(written)} values for {alias}.",
        }

    def inspect(self, form: dict[str, str]) -> dict[str, Any]:
        """Look at the sign-in page and report which credentials it asks for.

        Nothing can be assumed here. A form may want a PIN, a clinic code or a
        tenant as well as a username and a password, and the only way to know is
        to open it. No credential is involved in this step at all.
        """
        app_url = (form.get("app_url") or "").strip()
        if not app_url:
            return {"ok": False, "problems": ["Give the address of your app first."]}
        if not app_url.startswith(("http://", "https://")):
            app_url = "https://" + app_url
        login_path = (form.get("login_path") or "/login").strip() or "/login"
        if not login_path.startswith("/"):
            login_path = "/" + login_path

        try:
            found = asyncio.run(_look_at_login(app_url, login_path))
        except Exception as exc:
            return {"ok": False, "problems": [f"Could not open that page: {exc}"]}

        if not found.complete:
            return {"ok": False, "problems": found.problems or ["I could not read that form."]}

        fields = [
            {"name": "username", "label": "Username or email", "kind": "text"},
            {"name": "password", "label": "Password", "kind": "password"},
        ]
        for name, extra in found.extras.items():
            fields.append(
                {
                    "name": name,
                    "label": _label_for(name, extra),
                    "kind": "password",  # an unknown extra credential is a secret
                }
            )
        return {"ok": True, "fields": fields, "found": found.username.describe()}

    def provision(self, form: dict[str, str]) -> dict[str, Any]:
        """Run the wizard with the form's answers. Returns a safe summary."""
        from qa_copilot.sanitize import sanitizer
        from qa_copilot.setup.writer import free_account_name
        from qa_copilot.setup.wizard import run_wizard

        app_url = (form.get("app_url") or "").strip()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not app_url or not username or not password:
            return {"state": "failed", "detail": "Address, username and password are all needed."}

        environment = (form.get("environment") or "local").strip() or "local"
        account = free_account_name(
            self.config_dir, (form.get("account") or "admin").strip() or "admin", environment
        )
        capabilities = ", ".join(form.get("capabilities", "browse").split(",")) or "browse"

        extra_values = {
            key[len("extra_") :]: value
            for key, value in form.items()
            if key.startswith("extra_") and value
        }

        answers = iter(["y", account, capabilities, username])

        def reader(_prompt: str = "") -> str:
            return next(answers)

        # run_wizard narrates to stdout, which in the MCP server is the protocol
        # channel. Capture it, and scrub it before it is ever shown.
        narration = io.StringIO()
        try:
            with contextlib.redirect_stdout(narration):
                code = asyncio.run(
                    run_wizard(
                        config_dir=self.config_dir,
                        url=app_url,
                        environment=environment,
                        login_path=(form.get("login_path") or "/login").strip() or "/login",
                        headless=True,
                        secrets_file=self.secrets_file,
                        work_dir=self.work_dir,
                        reader=reader,
                        password_reader=lambda _p="": password,
                        extra_values=extra_values,
                    )
                )
        except Exception as exc:  # a broken address, an unreachable host, a crash
            return {"state": "failed", "detail": f"Setup could not finish: {exc}"}
        finally:
            del password  # do not leave it in this frame for a traceback to print

        detail = sanitizer.scrub(_plain(narration.getvalue()))
        if code != 0:
            return {"state": "failed", "detail": detail or "Setup did not complete."}

        alias = account.upper().replace("-", "_")
        if not alias.endswith("USER"):
            alias += "_USER"
        return {
            "state": "done",
            "environment": environment,
            "alias": alias,
            "url": app_url,
            "detail": detail,
        }


def pending_identities(config_dir: Path) -> list[dict[str, Any]]:
    """Accounts that are configured but have no usable credentials yet.

    The first version of this page could only ever add a *new* environment and
    account. That is the wrong shape for the commonest case: an identity already
    written into identities.yaml — by hand, or by a colleague — whose secrets
    were never filled in. Those accounts could not be completed here at all.
    """
    import os

    from qa_copilot.config import load_config
    from qa_copilot.secrets.env import load_dotenv, ref_to_env_var

    try:
        config = load_config(config_dir)
    except Exception:
        return []

    # Values already in the .env count as configured. Without this the page
    # offers to re-enter credentials that are perfectly fine.
    if config.dotenv_path:
        load_dotenv(config.dotenv_path)

    pending = []
    for alias, identity in sorted(config.identities.items()):
        refs = {"username": identity.username_ref, "password": identity.password_ref}
        refs.update(identity.extra_refs)
        if not identity.username_ref or not identity.password_ref:
            continue
        if all(ref and ref_to_env_var(ref) in os.environ for ref in refs.values()):
            continue  # already usable
        environment = identity.environments[0] if identity.environments else None
        pending.append(
            {
                "alias": alias,
                "environment": environment,
                "description": identity.description or "",
                # username and password always; then whatever else this login
                # form demands — a PIN, a tenant, a second factor's static code.
                "fields": ["username", "password", *sorted(identity.extra_refs)],
            }
        )
    return pending


async def _verify_login(config_dir: Path, environment: str, alias: str, values: dict) -> str:
    """Sign in for real before anything is written. Returns '' when it worked."""
    from qa_copilot.config import load_config
    from qa_copilot.executor.browser import open_session
    from qa_copilot.identity.broker import Credentials
    from qa_copilot.secrets.base import SecretValue

    config = load_config(config_dir)
    env = config.environment(environment)
    if env.login is None:
        return f"environment {environment!r} has no sign-in recipe to check against"

    creds = Credentials(
        identity=alias,
        username=SecretValue(f"{alias}.username", values["username"]),
        password=SecretValue(f"{alias}.password", values["password"]),
        extras={
            k: SecretValue(f"{alias}.{k}", v)
            for k, v in values.items()
            if k not in ("username", "password")
        },
    )
    session = await open_session(env, config.artifacts_path, headless=True)
    try:
        result = await session.login(env.login, creds)
    except Exception as exc:
        return f"could not complete the sign-in form: {exc}"
    finally:
        await session.close()
    if not result.get("authenticated"):
        return result.get("detail") or "the sign-in did not succeed with those details"
    return ""


async def _look_at_login(app_url: str, login_path: str):
    """Open the sign-in page in a real browser and report what is on it."""
    from urllib.parse import urlparse

    from qa_copilot.config import Environment
    from qa_copilot.executor.browser import open_session
    from qa_copilot.setup.discover import discover_login

    parsed = urlparse(app_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    session = await open_session(
        Environment(name="setup-probe", base_url=base), Path("artifacts"), headless=True
    )
    try:
        return await discover_login(session.page, base + login_path)
    finally:
        await session.close()


def _label_for(name: str, extra: Any) -> str:
    """Prefer what the page itself calls the field; fall back to its key."""
    label = (extra.detail or {}).get("label") or (extra.detail or {}).get("placeholder")
    return str(label).strip() if label else name.replace("_", " ").title()


def _plain(text: str) -> str:
    """Strip the wizard's terminal colour codes for display in a browser."""
    import re

    return re.sub(r"\033\[[0-9;]*m", "", text).strip()


def _handler(session: SetupSession):
    class Handler(BaseHTTPRequestHandler):
        # Silence the default stderr access log; this process may be speaking
        # MCP and the noise is not useful to anyone.
        def log_message(self, *_args: Any) -> None:
            return

        def _authorised(self, query: dict[str, list[str]]) -> bool:
            given = (query.get("t") or [""])[0]
            return secrets.compare_digest(given, session.token)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # This page must never be embedded or fetched by another origin.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802 - http.server's interface
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if not self._authorised(query):
                self._send(403, b"Not for you.", "text/plain")
                return
            if parsed.path == "/status":
                self._json(200, session.public_status())
                return
            if parsed.path != "/":
                self._send(404, b"No.", "text/plain")
                return
            self._send(200, page(session).encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in ("/submit", "/fill", "/inspect"):
                self._send(404, b"No.", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            form = {k: (v[0] if v else "") for k, v in parse_qs(raw, keep_blank_values=True).items()}
            if not secrets.compare_digest(form.get("t", ""), session.token):
                self._json(403, {"state": "failed", "detail": "Wrong or missing token."})
                return

            if parsed.path == "/inspect":
                self._json(200, session.inspect(form))
                return

            session.set_status(state="running", detail="Signing in to check the details…")
            if parsed.path == "/fill":
                result = session.fill_existing(form)
            else:
                result = session.provision(form)
            session.set_status(**result)
            self._json(200, session.public_status())

    return Handler


def start(
    config_dir: Path, secrets_file: Path, work_dir: Path, *, port: int = 0
) -> SetupSession:
    """Start the setup page on the loopback interface and return the session."""
    session = SetupSession(config_dir, secrets_file, work_dir)
    # 127.0.0.1, never 0.0.0.0: nothing outside this machine may reach the form.
    session.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(session))
    thread = threading.Thread(target=session.httpd.serve_forever, daemon=True)
    thread.start()
    return session


def page(session: SetupSession) -> str:
    checkboxes = "\n".join(
        f'<label class="cap"><input type="checkbox" name="cap" value="{name}"'
        f'{" checked" if name == "browse" else ""}> <b>{name}</b>'
        f"<span>{html.escape(meaning)}</span></label>"
        for name, meaning in CAPABILITIES
    )
    return (
        _PAGE.replace("{{TOKEN}}", html.escape(session.token))
        .replace("{{CAPABILITIES}}", checkboxes)
        .replace("{{PENDING}}", _pending_html(session.config_dir))
    )


_FIELD_LABELS = {
    "username": ("Username or email", "text"),
    "password": ("Password", "password"),
}


def _pending_html(config_dir: Path) -> str:
    """The section for accounts that exist but cannot sign in yet."""
    accounts = pending_identities(config_dir)
    if not accounts:
        return ""

    cards = []
    for a in accounts:
        rows = []
        for field in a["fields"]:
            label, kind = _FIELD_LABELS.get(field, (field.upper(), "password"))
            rows.append(
                f'<label class="f"><span class="t">{html.escape(label)}</span>'
                f'<input type="{kind}" name="field_{html.escape(field)}" required></label>'
            )
        where = f' <span>on {html.escape(a["environment"])}</span>' if a["environment"] else ""
        desc = f'<p class="desc">{html.escape(a["description"])}</p>' if a["description"] else ""
        cards.append(
            f'<form class="card acct" data-alias="{html.escape(a["alias"])}">'
            f'<div class="who"><b>{html.escape(a["alias"])}</b>{where}</div>{desc}'
            + "".join(rows)
            + '<button type="submit">Check these details and save</button>'
            '<div class="out"></div></form>'
        )

    return (
        '<h2 class="sec">Accounts waiting for credentials</h2>'
        '<p class="sub">These are already set up, but cannot sign in until you '
        "add their details. Everything stays on this machine.</p>" + "".join(cards)
    )


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up QA Copilot</title>
<style>
  :root {
    --bg:#f6f7f9; --card:#fff; --ink:#16181d; --dim:#5b6270; --line:#e2e5ea;
    --accent:#2f6df6; --ok:#0d7a4a; --bad:#b3261e; --note:#fff8e1; --noteline:#e8d48b;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#14161a; --card:#1c1f25; --ink:#e9ecf1; --dim:#9aa3b2; --line:#2c313a;
      --accent:#6f9bff; --ok:#4ade80; --bad:#ff7b72; --note:#2a2415; --noteline:#5c4f22;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:640px;margin:0 auto;padding:40px 20px 80px}
  h1{font-size:24px;margin:0 0 6px}
  .sub{color:var(--dim);margin:0 0 24px}
  .note{background:var(--note);border:1px solid var(--noteline);border-radius:10px;
        padding:14px 16px;margin:0 0 24px;font-size:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
  label.f{display:block;margin:0 0 18px}
  label.f > span.t{display:block;font-weight:600;margin-bottom:4px}
  label.f > span.h{display:block;color:var(--dim);font-size:13px;margin-bottom:6px}
  input[type=text],input[type=password]{width:100%;padding:10px 12px;border-radius:8px;
    border:1px solid var(--line);background:var(--bg);color:var(--ink);font-size:15px}
  input:focus{outline:2px solid var(--accent);outline-offset:1px}
  .row{display:flex;gap:14px}.row>*{flex:1}
  fieldset{border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 18px}
  legend{font-weight:600;padding:0 6px}
  label.cap{display:flex;align-items:baseline;gap:8px;margin:6px 0;font-size:14px}
  label.cap span{color:var(--dim);font-size:13px}
  button{background:var(--accent);color:#fff;border:0;border-radius:9px;
         padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
  button[disabled]{opacity:.6;cursor:default}
  #out{margin-top:22px;white-space:pre-wrap;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
       border-radius:10px;padding:0}
  #out.show{padding:14px 16px;border:1px solid var(--line);background:var(--card)}
  .ok{color:var(--ok)} .bad{color:var(--bad)}
  h2.sec{font-size:17px;margin:32px 0 4px}
  h3.sec{font-size:14px;margin:22px 0 12px;color:var(--dim);
         text-transform:uppercase;letter-spacing:.04em}
  #look{background:transparent;color:var(--accent);border:1px solid var(--accent)}
  h2.sec:first-of-type{margin-top:0}
  .acct{margin-bottom:16px}
  .acct .who{font-size:16px;margin-bottom:2px}
  .acct .who span{color:var(--dim);font-weight:400;font-size:14px}
  .acct .desc{color:var(--dim);font-size:13px;margin:0 0 16px}
  .acct .out{margin-top:14px;white-space:pre-wrap;
    font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
</style></head><body><div class="wrap">
<h1>Set up QA Copilot</h1>
<p class="sub">Point it at the application you want to test.</p>

<div class="note"><b>This page is on your machine only.</b> The AI assistant
cannot see it. The password you type goes straight into your local secret
store — the assistant is told the account's <i>nickname</i> and what it is
allowed to do, never the credential.</div>

{{PENDING}}

<h2 class="sec">Connect an application</h2>
<form class="card" id="f" autocomplete="off">
  <input type="hidden" name="t" value="{{TOKEN}}">

  <label class="f"><span class="t">Web address of your app</span>
    <span class="h">If it runs on your own machine, something like http://localhost:3000</span>
    <input type="text" name="app_url" placeholder="http://localhost:3000" required></label>

  <div class="row">
    <label class="f"><span class="t">Call this environment</span>
      <span class="h">A short word you will use in tests</span>
      <input type="text" name="environment" value="local"></label>
    <label class="f"><span class="t">Sign-in page</span>
      <span class="h">The part after the address</span>
      <input type="text" name="login_path" value="/login"></label>
  </div>

  <label class="f"><span class="t">Nickname for this test account</span>
    <span class="h">"admin" becomes ADMIN_USER — this is what the assistant sees</span>
    <input type="text" name="account" value="admin"></label>

  <fieldset><legend>What is this account allowed to do?</legend>
    {{CAPABILITIES}}
  </fieldset>

  <button type="button" id="look">Look at the sign-in page</button>
  <div id="creds"></div>
  <button type="submit" id="go" hidden>Check these details and save</button>
  <div id="out"></div>
</form>

<script>
const TOKEN = "{{TOKEN}}";
document.querySelectorAll('form.acct').forEach((form) => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button'), box = form.querySelector('.out');
    btn.disabled = true; btn.textContent = 'Signing in to check…';
    box.textContent = 'Opening a browser to try these details.';
    const data = new URLSearchParams();
    data.append('t', TOKEN);
    data.append('alias', form.dataset.alias);
    for (const [k, v] of new FormData(form)) data.append(k, v);
    try {
      const r = await fetch('/fill', {method:'POST', body:data});
      const j = await r.json();
      if (j.state === 'done') {
        box.innerHTML = '<b class="ok">Saved.</b> ' + form.dataset.alias +
          ' can sign in now. Go back to your assistant and ask it to run the test.';
        btn.textContent = 'Saved';
        form.querySelectorAll('input').forEach(i => { i.value = ''; i.disabled = true; });
      } else {
        box.innerHTML = '<b class="bad">Not saved.</b>\\n' + (j.detail || '');
        btn.disabled = false; btn.textContent = 'Try again';
      }
    } catch (err) {
      box.innerHTML = '<b class="bad">Could not reach the setup service.</b>\\n' + err;
      btn.disabled = false; btn.textContent = 'Try again';
    }
  });
});

const f = document.getElementById('f'), out = document.getElementById('out'),
      go = document.getElementById('go'), look = document.getElementById('look'),
      creds = document.getElementById('creds');

// Step one. Which credentials a form wants cannot be guessed — some ask for a
// PIN or a clinic code as well — so the page is built from what is on it.
look.addEventListener('click', async () => {
  look.disabled = true; look.textContent = 'Opening your sign-in page…';
  out.className = 'show'; out.textContent = 'Looking at the form.';
  const data = new URLSearchParams();
  data.append('t', TOKEN);
  data.append('app_url', f.app_url.value);
  data.append('login_path', f.login_path.value);
  try {
    const j = await (await fetch('/inspect', {method:'POST', body:data})).json();
    if (!j.ok) {
      out.innerHTML = '<b class="bad">I could not read that sign-in page.</b>\\n\\n' +
        (j.problems || []).join('\\n');
      look.disabled = false; look.textContent = 'Try again';
      return;
    }
    creds.innerHTML = '<h3 class="sec">This form asks for</h3>' + j.fields.map(fl =>
      '<label class="f"><span class="t">' + fl.label + '</span>' +
      '<input type="' + fl.kind + '" data-field="' + fl.name + '" required></label>'
    ).join('');
    out.className = ''; out.textContent = '';
    look.hidden = true; go.hidden = false;
  } catch (err) {
    out.innerHTML = '<b class="bad">Could not reach the setup service.</b>\\n' + err;
    look.disabled = false; look.textContent = 'Try again';
  }
});

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  go.disabled = true; go.textContent = 'Signing in to check…';
  out.className = 'show'; out.textContent =
    'Opening a browser to look at your sign-in page. This can take a moment.';
  const data = new URLSearchParams();
  for (const [k, v] of new FormData(f)) {
    if (k === 'cap') continue;
    data.append(k, v);
  }
  data.append('capabilities',
    [...f.querySelectorAll('input[name=cap]:checked')].map(c => c.value).join(','));
  creds.querySelectorAll('[data-field]').forEach(i => {
    const name = i.dataset.field;
    data.append(name === 'username' || name === 'password' ? name : 'extra_' + name, i.value);
  });
  try {
    const r = await fetch('/submit', {method:'POST', body:data});
    const j = await r.json();
    if (j.state === 'done') {
      out.innerHTML = '<b class="ok">Done.</b>\\n\\n' +
        'Environment: ' + j.environment + '\\nAccount:     ' + j.alias +
        '\\n\\nGo back to your assistant and ask it to write a test. ' +
        'Refer to the account as ' + j.alias + ', or just say "an admin".';
      go.textContent = 'Saved'; creds.querySelectorAll('[data-field]').forEach(i => { i.value = ''; });
    } else {
      out.innerHTML = '<b class="bad">That did not work.</b>\\n\\n' +
        (j.detail || 'No detail.');
      go.disabled = false; go.textContent = 'Try again';
    }
  } catch (err) {
    out.innerHTML = '<b class="bad">Could not reach the setup service.</b>\\n' + err;
    go.disabled = false; go.textContent = 'Try again';
  }
});
</script>
</div></body></html>
"""
