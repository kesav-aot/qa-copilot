"""A deliberately small target application, so the PoC runs end to end without
pointing at anything real.

Two roles, a login form, an admin-only user management page, and a token API.
Credentials live in this file *only* because it is a throwaway demo target — the
point of the exercise is that QA Copilot reads them from the secret store and the
model never sees them.
"""

from __future__ import annotations

import secrets
from functools import wraps

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

USERS = {
    "admin@qa.local": {
        "password": "Adm1n-Demo-Pass!",
        "name": "Ada Admin",
        "role": "admin",
    },
    "user@qa.local": {
        "password": "Us3r-Demo-Pass!",
        "name": "Sam Standard",
        "role": "standard",
    },
}

def _initial_users():
    return [
        {"id": 1, "name": "Rae Rivera", "email": "rae@qa.local", "status": "active"},
        {"id": 2, "name": "Kit Osei", "email": "kit@qa.local", "status": "active"},
        {"id": 3, "name": "Noor Haddad", "email": "noor@qa.local", "status": "disabled"},
    ]


MANAGED_USERS = _initial_users()

API_TOKENS: dict[str, str] = {}

LAYOUT = """
<!doctype html><meta charset=utf-8>
<title>{{ title }} · Demo Shop Admin</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#16191d}
 header{background:#1f2430;color:#fff;padding:12px 20px;display:flex;gap:20px;align-items:center}
 header a{color:#cfd6e4;text-decoration:none}
 main{max-width:820px;margin:32px auto;background:#fff;padding:28px;border-radius:10px;
      box-shadow:0 1px 3px rgba(0,0,0,.09)}
 label{display:block;margin:14px 0 4px;font-weight:600;font-size:14px}
 input{padding:9px 11px;width:280px;border:1px solid #c4cad4;border-radius:6px;font-size:15px}
 button{margin-top:18px;padding:9px 18px;border:0;border-radius:6px;background:#2f6feb;
        color:#fff;font-size:15px;cursor:pointer}
 table{border-collapse:collapse;width:100%;margin-top:16px}
 th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #e6e9ee;font-size:15px}
 .error{color:#b3261e;font-weight:600;margin-top:14px}
 .pill{padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}
 .active{background:#e3f5e8;color:#186b32} .disabled{background:#f3e4e3;color:#8b2c24}
</style>
<header>
  <strong>Demo Shop Admin</strong>
  {% if user %}
    <a href="/dashboard" data-testid="nav-dashboard">Dashboard</a>
    {% if user.role == 'admin' %}<a href="/users" data-testid="nav-users">Users</a>{% endif %}
    <span style="margin-left:auto" data-testid="current-user">{{ user.name }}</span>
    <a href="/logout" data-testid="nav-logout">Sign out</a>
  {% endif %}
</header>
<main>{{ body|safe }}</main>
"""


def render(title: str, body: str, **ctx):
    user = session.get("user")
    return render_template_string(
        LAYOUT, title=title, body=render_template_string(body, user=user, **ctx), user=user
    )


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)

    return wrapper


@app.get("/")
def index():
    return redirect(url_for("dashboard") if "user" in session else url_for("login"))


# A second sign-in form that wants a third credential, the way some clinical and
# banking systems ask for a second-level PIN. It exists so the setup flow can be
# exercised against a form whose fields cannot be guessed in advance.
SECOND_LEVEL_PIN = "4821"


# A link that opens in a new tab, the way a product or document listing does.
# window.open was already handled; target=_blank was not, and that is the form
# a shop's search results actually use.
@app.get("/listing")
def listing():
    return render(
        "Listing",
        """
          <h1>Results</h1>
          <a href="/detail" target="_blank" data-testid=first-result>OnePlus Nord Buds 3r</a>
        """,
    )


@app.get("/detail")
def detail():
    return render("Detail", "<h1>OnePlus Nord Buds 3r</h1><p>Add to Cart</p>")


@app.route("/pin-login", methods=["GET", "POST"])
def pin_login():
    error = None
    if request.method == "POST":
        email = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        pin = request.form.get("pin") or ""
        record = USERS.get(email)
        if (
            record
            and secrets.compare_digest(record["password"], password)
            and secrets.compare_digest(SECOND_LEVEL_PIN, pin)
        ):
            session["user"] = {"email": email, "name": record["name"], "role": record["role"]}
            return redirect(url_for("dashboard"))
        error = "Invalid username, password or PIN."
    body = """
      <h1>Sign in</h1>
      <form method=post>
        <label for=username>User name</label>
        <input id=username name=username autocomplete=username>
        <label for=password>Password</label>
        <input id=password name=password type=password autocomplete=current-password>
        <label for=pin>2nd Level Passcode</label>
        <input id=pin name=pin type=password autocomplete=off>
        <input type=hidden name=csrf value=not-a-real-token>
        <br><button type=submit>Sign In</button>
      </form>
      {% if error %}<p class=error data-testid=login-error>{{ error }}</p>{% endif %}
    """
    return render("Sign in", body, error=error), (401 if error else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        record = USERS.get(email)
        if record and secrets.compare_digest(record["password"], password):
            session["user"] = {"email": email, "name": record["name"], "role": record["role"]}
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    body = """
      <h1>Sign in</h1>
      <form method=post>
        <label for=username>Email</label>
        <input id=username name=username data-testid=login-username autocomplete=username>
        <label for=password>Password</label>
        <input id=password name=password type=password data-testid=login-password
               autocomplete=current-password>
        <br><button type=submit data-testid=login-submit>Sign in</button>
      </form>
      {% if error %}<p class=error data-testid=login-error>{{ error }}</p>{% endif %}
    """
    return render("Sign in", body, error=error), (401 if error else 200)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/dashboard")
@login_required
def dashboard():
    body = """
      <h1 data-testid=dashboard-heading>Dashboard</h1>
      <p>Welcome back, {{ user.name }}. Your role is <strong>{{ user.role }}</strong>.</p>
      <p data-testid=orders-today>Orders today: 42</p>
    """
    return render("Dashboard", body)


@app.get("/users")
@login_required
def users():
    if session["user"]["role"] != "admin":
        body = """
          <h1 data-testid=forbidden-heading>Access denied</h1>
          <p>You do not have permission to manage users.</p>
        """
        return render("Access denied", body), 403
    body = """
      <h1 data-testid=users-heading>User Management</h1>
      <table>
        <tr><th>Name</th><th>Email</th><th>Status</th><th></th></tr>
        {% for u in users %}
        <tr data-testid="user-row-{{ u.id }}">
          <td>{{ u.name }}</td><td>{{ u.email }}</td>
          <td><span class="pill {{ u.status }}" data-testid="user-status-{{ u.id }}">{{ u.status }}</span></td>
          <td>
            {% if u.status == 'active' %}
              <form method=post action="/users/{{ u.id }}/disable" style=margin:0>
                <button type=submit data-testid="disable-user-{{ u.id }}"
                        style="margin:0;background:#b3261e">Disable</button>
              </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    """
    return render("Users", body, users=MANAGED_USERS)


@app.post("/users/<int:user_id>/disable")
@login_required
def disable_user(user_id: int):
    if session["user"]["role"] != "admin":
        abort(403)
    for u in MANAGED_USERS:
        if u["id"] == user_id:
            u["status"] = "disabled"
    return redirect(url_for("users"))


# --- API -------------------------------------------------------------------
@app.post("/api/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    record = USERS.get(payload.get("username", ""))
    if record and secrets.compare_digest(record["password"], payload.get("password", "")):
        token = secrets.token_urlsafe(32)
        API_TOKENS[token] = payload["username"]
        return jsonify({"token": token})
    return jsonify({"error": "invalid_credentials"}), 401


def _api_user():
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip()
    email = API_TOKENS.get(token)
    record = USERS.get(email or "")
    if not record:
        return None
    return {"email": email, "name": record["name"], "role": record["role"]}


@app.get("/api/me")
def api_me():
    user = _api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"email": user["email"], "name": user["name"], "role": user["role"]})


@app.get("/api/users")
def api_users():
    user = _api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user["role"] != "admin":
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"users": MANAGED_USERS})


@app.post("/api/test-reset")
def api_test_reset():
    """Put the demo data back. Only exists because this is a throwaway target
    app — a test that disables a user would otherwise change what the next run
    sees. A real application under test would need fixtures instead."""
    MANAGED_USERS[:] = _initial_users()
    API_TOKENS.clear()
    return jsonify({"reset": True, "users": len(MANAGED_USERS)})


if __name__ == "__main__":
    app.run(port=8099, debug=False)
