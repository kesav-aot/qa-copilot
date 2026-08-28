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
            if parsed.path != "/submit":
                self._send(404, b"No.", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            form = {k: (v[0] if v else "") for k, v in parse_qs(raw, keep_blank_values=True).items()}
            if not secrets.compare_digest(form.get("t", ""), session.token):
                self._json(403, {"state": "failed", "detail": "Wrong or missing token."})
                return

            session.set_status(state="running", detail="Looking at your app's sign-in page…")
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
    return _PAGE.replace("{{TOKEN}}", html.escape(session.token)).replace(
        "{{CAPABILITIES}}", checkboxes
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
</style></head><body><div class="wrap">
<h1>Set up QA Copilot</h1>
<p class="sub">Point it at the application you want to test.</p>

<div class="note"><b>This page is on your machine only.</b> The AI assistant
cannot see it. The password you type goes straight into your local secret
store — the assistant is told the account's <i>nickname</i> and what it is
allowed to do, never the credential.</div>

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

  <label class="f"><span class="t">Username or email</span>
    <span class="h">Use a test account, never a personal or production login</span>
    <input type="text" name="username" required></label>

  <label class="f"><span class="t">Password</span>
    <span class="h">Stored locally, in a file only you can read</span>
    <input type="password" name="password" required></label>

  <button type="submit" id="go">Check these details and save</button>
  <div id="out"></div>
</form>

<script>
const f = document.getElementById('f'), out = document.getElementById('out'),
      go = document.getElementById('go');
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
  try {
    const r = await fetch('/submit', {method:'POST', body:data});
    const j = await r.json();
    if (j.state === 'done') {
      out.innerHTML = '<b class="ok">Done.</b>\\n\\n' +
        'Environment: ' + j.environment + '\\nAccount:     ' + j.alias +
        '\\n\\nGo back to your assistant and ask it to write a test. ' +
        'Refer to the account as ' + j.alias + ', or just say "an admin".';
      go.textContent = 'Saved'; f.querySelector('[name=password]').value = '';
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
