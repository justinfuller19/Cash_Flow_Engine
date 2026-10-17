"""Shared household login and isolated per-household SQLite databases."""
from __future__ import annotations
import hashlib, hmac, os, secrets, sqlite3, time
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from . import database

SESSION_COOKIE = "cashflow_session"
SESSION_SECONDS = 604800
_tenant: ContextVar[int | None] = ContextVar("tenant", default=None)
_initialized_auth_paths: set[str] = set()
_init_lock = Lock()
LOGIN_ATTEMPT_LIMIT = 10
LOGIN_WINDOW_SECONDS = 900
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = Lock()

def set_current_household(household_id: int):
    return _tenant.set(household_id)

def reset_current_household(token) -> None:
    _tenant.reset(token)

def current_household_id() -> int | None:
    return _tenant.get()

def auth_database_path() -> Path:
    base = database.base_database_path()
    return base.with_name(base.stem + ".auth.sqlite")

@contextmanager
def auth_connect():
    path = auth_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def initialize_auth_database():
    path_key = str(auth_database_path().resolve())
    with _init_lock:
        if path_key in _initialized_auth_paths and Path(path_key).is_file():
            return
        with auth_connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS households (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              household_name TEXT NOT NULL,
              username TEXT NOT NULL COLLATE NOCASE UNIQUE,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS household_sessions (
              token_hash TEXT PRIMARY KEY,
              household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON household_sessions(expires_at);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(households)")}
            if "notification_email" not in columns:
                db.execute("ALTER TABLE households ADD COLUMN notification_email TEXT")
        _initialized_auth_paths.add(path_key)


def _prune_login_attempts(key: str, now: float) -> list[float]:
    cutoff = now - LOGIN_WINDOW_SECONDS
    return [attempt for attempt in _login_attempts.get(key, []) if attempt > cutoff]


def login_is_rate_limited(key: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _login_attempts_lock:
        attempts = _prune_login_attempts(key, now)
        if attempts:
            _login_attempts[key] = attempts
        else:
            _login_attempts.pop(key, None)
        return len(attempts) >= LOGIN_ATTEMPT_LIMIT


def record_login_failure(key: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _login_attempts_lock:
        _login_attempts[key] = _prune_login_attempts(key, now) + [now]


def clear_login_failures(key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(key, None)

def household_count() -> int:
    initialize_auth_database()
    with auth_connect() as db:
        return db.execute("SELECT COUNT(*) FROM households").fetchone()[0]

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"scrypt$" + salt.hex() + "$" + digest.hex()

def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, digest = encoded.split("$", 2)
        if scheme != "scrypt": return False
        expected = bytes.fromhex(digest)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def create_household(name: str, username: str, password: str) -> int:
    initialize_auth_database()
    name, username = name.strip(), username.strip()
    if not 1 <= len(name) <= 120: raise ValueError("Household name must be 1–120 characters.")
    if not 3 <= len(username) <= 254 or any(c.isspace() for c in username):
        raise ValueError("Username/email must be 3–254 characters without spaces.")
    if len(password) < 12: raise ValueError("Password must be at least 12 characters.")
    with auth_connect() as db:
        try:
            cur = db.execute("INSERT INTO households(household_name,username,password_hash) VALUES(?,?,?)",
                             (name, username, _hash_password(password)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username/email is already registered.") from exc
        return int(cur.lastrowid)

def authenticate(username: str, password: str) -> int | None:
    initialize_auth_database()
    with auth_connect() as db:
        row = db.execute("SELECT id,password_hash FROM households WHERE username=? COLLATE NOCASE",
                         (username.strip(),)).fetchone()
    return int(row["id"]) if row and _verify_password(password, row["password_hash"]) else None

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()

def issue_session(household_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with auth_connect() as db:
        db.execute("DELETE FROM household_sessions WHERE expires_at <= ?", (int(time.time()),))
        db.execute("INSERT INTO household_sessions VALUES(?,?,?)",
                   (_token_hash(token), household_id, int(time.time()) + SESSION_SECONDS))
    return token

def resolve_session(token: str | None) -> int | None:
    if not token: return None
    initialize_auth_database()
    with auth_connect() as db:
        row = db.execute("SELECT household_id FROM household_sessions WHERE token_hash=? AND expires_at>?",
                         (_token_hash(token), int(time.time()))).fetchone()
    return int(row["household_id"]) if row else None

def revoke_session(token: str | None):
    if token:
        with auth_connect() as db:
            db.execute("DELETE FROM household_sessions WHERE token_hash=?", (_token_hash(token),))

def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · CashFlow Engine</title><style>
body{{font:16px system-ui;background:#f3f6fa;color:#18212f;margin:0;padding:24px;min-height:90vh;display:grid;place-items:center}}
main{{background:white;padding:28px;border-radius:16px;box-shadow:0 8px 30px #172b4d12;width:min(100%,420px);box-sizing:border-box}}
h1{{color:#155e75;margin-top:0}}label{{display:block;margin:14px 0 5px}}input{{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box;font:inherit}}
button{{margin-top:20px;width:100%;padding:12px;border:0;border-radius:8px;background:#0e7490;color:white;font:inherit;font-weight:700}}
.muted{{color:#5b6678}}.error{{background:#fee2e2;padding:10px;border-radius:8px;color:#991b1b}}a{{color:#0e7490}}</style><body><main><h1>CashFlow Engine</h1>{body}</main></body></html>''')

def login_page(error: str = ""):
    msg = f'<p class="error">{error}</p>' if error else ""
    return _page("Household login", f'''{msg}<p class="muted">Sign in to your shared household budget.</p>
<form method="post" action="/login"><label>Username or email</label><input name="username" autocomplete="username" required maxlength="254">
<label>Password</label><input name="password" type="password" autocomplete="current-password" required><button>Sign in</button></form>
<p class="muted">New household? <a href="/register">Create an account</a></p>''')

def register_page(error: str = ""):
    msg = f'<p class="error">{error}</p>' if error else ""
    return _page("Create household", f'''{msg}<p class="muted">One login is shared by your household. Each household's budget is private.</p>
<form method="post" action="/register"><label>Household name</label><input name="household_name" required maxlength="120">
<label>Username or email</label><input name="username" required minlength="3" maxlength="254" autocomplete="username">
<label>Password (at least 12 characters)</label><input name="password" type="password" required minlength="12" autocomplete="new-password">
<button>Create household account</button></form><p class="muted"><a href="/login">Already registered? Sign in</a></p>''')

async def household_auth_guard(request: Request, call_next):
    initialize_auth_database()
    path = request.url.path
    if path == "/health" or path in {"/login", "/register", "/logout"}:
        return await call_next(request)
    count = household_count()
    household_id = resolve_session(request.cookies.get(SESSION_COOKIE))
    if household_id is None:
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Please sign in to access household data."})
        return RedirectResponse("/register" if count == 0 else "/login", status_code=303)
    token = set_current_household(household_id)
    try:
        return await call_next(request)
    finally:
        reset_current_household(token)

async def login_route(request: Request):
    from urllib.parse import parse_qs
    client_key = request.client.host if request.client else "unknown"
    if login_is_rate_limited(client_key):
        response = login_page("Too many failed attempts. Wait 15 minutes before trying again.")
        response.status_code = 429
        return response
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    household_id = authenticate(form.get("username", [""])[0], form.get("password", [""])[0])
    if household_id is None:
        record_login_failure(client_key)
        return login_page("The username or password was incorrect.")
    clear_login_failures(client_key)
    token = issue_session(household_id)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
        secure=os.getenv("CASHFLOW_COOKIE_SECURE", "").lower() in {"1","true","yes"}, samesite="lax", path="/")
    return response

async def register_route(request: Request):
    from urllib.parse import parse_qs
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    try:
        household_id = create_household(form.get("household_name", [""])[0],
            form.get("username", [""])[0], form.get("password", [""])[0])
        token = set_current_household(household_id)
        try: database.initialize_database()
        finally: reset_current_household(token)
    except ValueError as exc:
        return register_page(str(exc))
    session = issue_session(household_id)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, httponly=True,
        secure=os.getenv("CASHFLOW_COOKIE_SECURE", "").lower() in {"1","true","yes"}, samesite="lax", path="/")
    return response

async def logout_route(request: Request):
    revoke_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return response



def household_account(household_id: int) -> dict:
    initialize_auth_database()
    with auth_connect() as db:
        row = db.execute(
            "SELECT household_name, username, notification_email FROM households WHERE id = ?",
            (household_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Household account not found.")
    return dict(row)


def update_household_account(
    household_id: int,
    current_password: str,
    notification_email: str,
    new_password: str = "",
) -> bool:
    """Update the household notification address and optionally rotate its password.

    Returns True when the password changed, allowing the caller to rotate sessions.
    """
    notification_email = notification_email.strip()
    if notification_email and (
        "@" not in notification_email
        or any(char.isspace() for char in notification_email)
        or len(notification_email) > 254
    ):
        raise ValueError("Enter a valid notification email address or leave it blank.")
    if new_password and len(new_password) < 12:
        raise ValueError("New password must be at least 12 characters.")
    with auth_connect() as db:
        row = db.execute(
            "SELECT password_hash FROM households WHERE id = ?", (household_id,)
        ).fetchone()
        if row is None or not _verify_password(current_password, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        if new_password:
            db.execute(
                "UPDATE households SET notification_email = ?, password_hash = ? WHERE id = ?",
                (notification_email or None, _hash_password(new_password), household_id),
            )
            db.execute("DELETE FROM household_sessions WHERE household_id = ?", (household_id,))
            return True
        db.execute(
            "UPDATE households SET notification_email = ? WHERE id = ?",
            (notification_email or None, household_id),
        )
        return False


def account_page(error: str = "", success: str = "") -> HTMLResponse:
    household_id = current_household_id()
    if household_id is None:
        return RedirectResponse("/login", status_code=303)
    account = household_account(household_id)
    import html
    message = f'<p class="error">{html.escape(error)}</p>' if error else ""
    notice = f'<p class="notice">{html.escape(success)}</p>' if success else ""
    email = html.escape(account["notification_email"] or "", quote=True)
    username = html.escape(account["username"])
    name = html.escape(account["household_name"])
    return _page("Household account", f"""{message}{notice}
<p class="muted">Manage notification delivery and the shared household password.</p>
<p><strong>{name}</strong><br><span class="muted">Login: {username}</span></p>
<form method="post" action="/account">
<label>Notification email (optional)</label><input name="notification_email" type="email" maxlength="254" value="{email}" autocomplete="email">
<p class="muted">Only this address receives this household's scheduled reports and reminders.</p>
<label>Current password (required to save)</label><input name="current_password" type="password" autocomplete="current-password" required>
<label>New password (optional; at least 12 characters)</label><input name="new_password" type="password" minlength="12" autocomplete="new-password">
<button>Save account settings</button></form>""")

async def account_update_route(request: Request):
    from urllib.parse import parse_qs
    household_id = current_household_id()
    if household_id is None:
        return RedirectResponse("/login", status_code=303)
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    try:
        password_changed = update_household_account(
            household_id,
            form.get("current_password", [""])[0],
            form.get("notification_email", [""])[0],
            form.get("new_password", [""])[0],
        )
    except ValueError as exc:
        return account_page(str(exc))
    if password_changed:
        session = issue_session(household_id)
        response = account_page(success="Password updated. Other household sessions were signed out.")
        response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, httponly=True,
            secure=os.getenv("CASHFLOW_COOKIE_SECURE", "").lower() in {"1","true","yes"},
            samesite="lax", path="/")
        return response
    return account_page(success="Household account settings saved.")


