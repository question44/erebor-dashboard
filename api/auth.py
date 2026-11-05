"""Dashboard accounts, sessions and per-user visibility, stored in SQLite."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

SESSION_COOKIE = "erebor_session"
SESSION_SECONDS = 30 * 24 * 3600
SESSION_RENEW_SECONDS = 24 * 3600
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 256
# Dashboard sections that can be hidden independently of their items.
MODULES = ("host-status", "outlet", "quick-actions", "services")
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
# Per-account dashboard arrangement: ordered and hidden card/service IDs.
LAYOUT_LISTS = ("cards", "services", "hiddenCards", "hiddenServices")
# Sections that can be folded to their first N rows.
LAYOUT_SECTIONS = ("cards", "services")
MAX_FOLD_ROWS = 50


def clean_folds(stored: dict) -> dict:
    folded = stored.get("folded")
    rows = stored.get("foldRows")
    return {
        "folded": [s for s in LAYOUT_SECTIONS if isinstance(folded, list) and s in folded],
        "foldRows": {s: rows[s] for s in LAYOUT_SECTIONS if isinstance(rows, dict) and type(rows.get(s)) is int
                     and 1 <= rows[s] <= MAX_FOLD_ROWS},
    }


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 * 1024 * 1024, dklen=32, **SCRYPT)
    encode = lambda value: base64.b64encode(value).decode()
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                maxmem=64 * 1024 * 1024, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Equalizes timing between unknown users and wrong passwords.
DUMMY_HASH = hash_password(secrets.token_hex(16))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password(password: str):
    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"Password must be {MIN_PASSWORD}–{MAX_PASSWORD} characters")


def normalize_username(username) -> str:
    username = username.strip().lower() if isinstance(username, str) else ""
    if not USERNAME.match(username):
        raise AuthError("Username must be 1–32 lowercase letters, digits, dots, dashes or underscores")
    return username


class Catalog:
    """The configured modules, services and actions an administrator can grant."""

    def __init__(self, settings: dict, enabled_actions: set):
        self.settings = settings
        self.services = [s for s in settings.get("services", []) if isinstance(s, dict) and s.get("id")]
        # Wake goes directly to NanoKVM; every other action needs the host API to allow it.
        self.actions = [a for a in settings.get("actions", [])
                        if (a == "wake" and str(settings.get("wakeUrl", "")).startswith("https://"))
                        or a in enabled_actions]
        self.modules = [m for m in MODULES if m != "outlet" or str(settings.get("outletUrl", "")).startswith("https://")]

    def describe(self):
        return {"modules": self.modules, "actions": self.actions,
                "services": [{"id": s["id"], "label": s.get("label", s["id"])} for s in self.services]}

    def clean(self, permissions) -> dict:
        if not isinstance(permissions, dict):
            raise AuthError("Invalid permissions")
        result = {}
        for key, allowed in [("modules", self.modules), ("services", [s["id"] for s in self.services]),
                             ("actions", self.actions)]:
            values = permissions.get(key, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise AuthError("Invalid permissions")
            # Unknown IDs are dropped so removing a service from settings cannot break accounts.
            result[key] = [v for v in allowed if v in values]
        return result

    def grant(self, user: dict) -> dict:
        """Effective permissions; administrators see everything configured."""
        if user["admin"]:
            return {"modules": list(self.modules), "services": [s["id"] for s in self.services],
                    "actions": list(self.actions)}
        return self.clean(user["permissions"])

    def view(self, user: dict) -> dict:
        grant = self.grant(user)
        modules = set(grant["modules"])
        actions = grant["actions"] if "quick-actions" in modules else []
        view = {key: self.settings.get(key, "") for key in ("title", "description", "domainSuffix")}
        view["auth"] = True
        view["actions"] = actions
        view["services"] = [s for s in self.services if s["id"] in grant["services"]] if "services" in modules else []
        # The power LED check also gates host actions, so it follows wake or host-status access.
        if "wake" in actions or "host-status" in modules:
            view["wakeUrl"] = self.settings.get("wakeUrl")
        view["hostStatus"] = "host-status" in modules
        if "outlet" in modules:
            view["outletUrl"] = self.settings.get("outletUrl")
        return {k: v for k, v in view.items() if v is not None}


class Throttle:
    """Per-account exponential lockout after repeated failed sign-ins."""

    def __init__(self, free_attempts=5, base=30, ceiling=900):
        self.free, self.base, self.ceiling = free_attempts, base, ceiling
        self.failures: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    def retry_after(self, username: str) -> int:
        with self.lock:
            count, until = self.failures.get(username, (0, 0))
            return max(0, int(until - time.monotonic() + 0.999))

    def failed(self, username: str):
        with self.lock:
            count, _ = self.failures.get(username, (0, 0))
            count += 1
            delay = 0 if count < self.free else min(self.ceiling, self.base * 2 ** (count - self.free))
            self.failures[username] = (count, time.monotonic() + delay)

    def succeeded(self, username: str):
        with self.lock:
            self.failures.pop(username, None)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.throttle = Throttle()
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password TEXT NOT NULL,
                    admin INTEGER NOT NULL DEFAULT 0,
                    permissions TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    expires REAL NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if "layout" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN layout TEXT NOT NULL DEFAULT '{}'")
                db.commit()
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    @staticmethod
    def _user(row) -> dict:
        return {"username": row["username"], "admin": bool(row["admin"]),
                "permissions": json.loads(row["permissions"])}

    def setup_required(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    def _new_session(self, db, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash(token), username, now + SESSION_SECONDS))
        return token

    def setup(self, username, password) -> str:
        username = normalize_username(username)
        validate_password(password)
        hashed = hash_password(password)
        with self.connect() as db:
            # The write lock makes the empty check and insert atomic across requests.
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise AuthError("Setup is already complete", 409)
            db.execute("INSERT INTO users (username, password, admin, permissions, created) VALUES (?, ?, 1, '{}', ?)",
                       (username, hashed, time.time()))
            return self._new_session(db, username)

    def login(self, username, password) -> str:
        try:
            username = normalize_username(username)
        except AuthError:
            raise AuthError("Incorrect username or password", 401)
        if not isinstance(password, str) or len(password) > MAX_PASSWORD:
            raise AuthError("Incorrect username or password", 401)
        wait = self.throttle.retry_after(username)
        if wait:
            raise AuthError(f"Too many attempts. Try again in {wait} seconds.", 429)
        with self.connect() as db:
            row = db.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
        if not verify_password(password, row["password"] if row else DUMMY_HASH) or not row:
            self.throttle.failed(username)
            raise AuthError("Incorrect username or password", 401)
        self.throttle.succeeded(username)
        with self.connect() as db:
            return self._new_session(db, username)

    def session(self, token):
        """Return (user, renewed) for a valid token; renewed sessions need a fresh cookie."""
        if not token or len(token) > 128:
            return None, False
        now = time.time()
        with self.connect() as db:
            row = db.execute("""SELECT users.*, sessions.expires FROM sessions JOIN users USING (username)
                                WHERE sessions.token = ? AND sessions.expires > ?""", (token_hash(token), now)).fetchone()
            if not row:
                return None, False
            renew = row["expires"] - now < SESSION_SECONDS - SESSION_RENEW_SECONDS
            if renew:
                db.execute("UPDATE sessions SET expires = ? WHERE token = ?", (now + SESSION_SECONDS, token_hash(token)))
            return self._user(row), renew

    def layout(self, username) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT layout FROM users WHERE username = ?", (username,)).fetchone()
        try:
            stored = json.loads(row["layout"]) if row else {}
        except ValueError:
            stored = {}
        layout = {key: [v for v in stored.get(key, []) if isinstance(v, str)] if isinstance(stored.get(key), list) else []
                  for key in LAYOUT_LISTS}
        layout["showHidden"] = stored.get("showHidden") is True
        return {**layout, **clean_folds(stored if isinstance(stored, dict) else {})}

    def set_layout(self, username, layout: dict):
        clean = {key: list(dict.fromkeys(layout.get(key, []))) for key in LAYOUT_LISTS}
        clean["showHidden"] = bool(layout.get("showHidden"))
        clean.update(clean_folds(layout))
        with self.connect() as db:
            if not db.execute("UPDATE users SET layout = ? WHERE username = ?",
                              (json.dumps(clean), username)).rowcount:
                raise AuthError("User not found", 404)

    def logout(self, token):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token = ?", (token_hash(token),))

    def users(self) -> list:
        with self.connect() as db:
            return [self._user(row) for row in db.execute("SELECT * FROM users ORDER BY username")]

    def create(self, username, password, admin, permissions):
        username = normalize_username(username)
        validate_password(password)
        hashed = hash_password(password)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise AuthError("That username already exists", 409)
            db.execute("INSERT INTO users (username, password, admin, permissions, created) VALUES (?, ?, ?, ?, ?)",
                       (username, hashed, int(bool(admin)), json.dumps(permissions), time.time()))

    def update(self, actor, username, *, password=None, admin=None, permissions=None, keep_token=None):
        hashed = None
        if password is not None:
            validate_password(password)
            hashed = hash_password(password)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise AuthError("User not found", 404)
            if admin is not None:
                if username == actor and not admin:
                    raise AuthError("You cannot remove your own administrator access", 409)
                db.execute("UPDATE users SET admin = ? WHERE username = ?", (int(bool(admin)), username))
            if permissions is not None:
                db.execute("UPDATE users SET permissions = ? WHERE username = ?", (json.dumps(permissions), username))
            if hashed:
                db.execute("UPDATE users SET password = ? WHERE username = ?", (hashed, username))
                # A password change signs out every other session for that account.
                db.execute("DELETE FROM sessions WHERE username = ? AND token != ?",
                           (username, token_hash(keep_token or "")))

    def change_password(self, username, current, new, keep_token):
        with self.connect() as db:
            row = db.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not isinstance(current, str) or not verify_password(current, row["password"]):
            raise AuthError("Current password is incorrect", 403)
        self.update(username, username, password=new, keep_token=keep_token)

    def delete(self, actor, username):
        if username == actor:
            raise AuthError("You cannot delete your own account", 409)
        with self.connect() as db:
            if not db.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount:
                raise AuthError("User not found", 404)
