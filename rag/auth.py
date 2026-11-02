"""Accounts, roles and sign-in for the Frontline Assistant.

- Passwords are stored only as salted scrypt hashes (Python standard library), never in plain text.
- Each user has one role; the role decides which assistants they can open and where they land.
- After 5 failed sign-ins an account is locked for 15 minutes.
- Sign-ins, sign-outs and account changes are written to an audit log (no passwords).

Accounts live in data/users.json on the server. Create them from the command line:

    python -m rag.auth create-admin itlead
    python -m rag.auth add jsmith --role revenue --name "Jo Smith"
    python -m rag.auth demo            # one account per role, random passwords printed once
    python -m rag.auth list

For production, the README describes connecting to Thameslink's single sign-on instead.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import config

USERS_FILE = config.STATE_DIR / "users.json"
AUDIT_FILE = config.STATE_DIR / "audit.jsonl"

MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
SESSION_IDLE_SECONDS = 30 * 60
MIN_PASSWORD_LEN = 10

# role id -> display name, user group, assistants (first = landing page)
ROLES: dict[str, dict] = {
    "frontline": {"name": "Frontline staff", "group": "Frontline employee / customer service agent",
                  "use_cases": ["policy"]},
    "revenue": {"name": "Revenue protection", "group": "Service desk agent / revenue protection officer",
                "use_cases": ["tickets", "policy"]},
    "assist": {"name": "Assisted travel", "group": "Resource planner / assisted travel coordinator",
               "use_cases": ["assist", "policy"]},
    "claims": {"name": "Customer relations", "group": "Customer relations agent",
               "use_cases": ["claims", "policy"]},
    "station": {"name": "Station management", "group": "Station manager / dispatch operator",
                "use_cases": ["fleet", "policy"]},
    "admin": {"name": "Administrator", "group": "IT / data protection lead",
              "use_cases": ["policy", "tickets", "assist", "claims", "fleet"], "admin": True},
}

_lock = threading.RLock()   # re-entrant: audit() is also called while the lock is held


@dataclass
class User:
    username: str
    name: str
    role: str
    salt: str
    pw_hash: str
    active: bool = True
    must_change: bool = True
    fails: int = 0
    locked_until: float = 0.0
    created: str = ""
    last_login: str = ""

    @property
    def use_cases(self) -> list[str]:
        return ROLES[self.role]["use_cases"]

    @property
    def is_admin(self) -> bool:
        return bool(ROLES[self.role].get("admin"))


class AuthError(Exception):
    pass


# ---------------------------------------------------------------- storage
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_users() -> dict[str, User]:
    if not USERS_FILE.exists():
        return {}
    data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {u["username"]: User(**u) for u in data["users"]}


def save_users(users: dict[str, User]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"users": [asdict(u) for u in users.values()]}, indent=1), encoding="utf-8")
    os.replace(tmp, USERS_FILE)          # atomic: never leaves a half-written file
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


def audit(event: str, username: str = "", by: str = "", detail: str = "") -> None:
    entry = {"time": _now(), "event": event, "user": username, "by": by, "detail": detail}
    with _lock, open(AUDIT_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_audit(limit: int = 200) -> list[dict]:
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(l) for l in reversed(lines) if l.strip()]


# ---------------------------------------------------------------- passwords
def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()


def _check(password: str, user: User) -> bool:
    _, digest = hash_password(password, bytes.fromhex(user.salt))
    return hmac.compare_digest(digest, user.pw_hash)


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"Use at least {MIN_PASSWORD_LEN} characters."
    if username and username.lower() in password.lower():
        return "The password must not contain the username."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[^A-Za-z]", password)):
        return "Use letters and at least one number or symbol."
    return None


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if re.search(r"\d", pw) and re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw):
            return pw


# ---------------------------------------------------------------- sign-in
_DUMMY_SALT, _DUMMY_HASH = hash_password("timing-equaliser")


def authenticate(username: str, password: str) -> User:
    """Return the user, or raise AuthError with a message safe to show on screen."""
    username = username.strip().lower()
    generic = "Wrong username or password."
    with _lock:
        users = load_users()
        user = users.get(username)
        if user is None:
            # spend the same time hashing, so response time does not reveal which usernames exist
            _check(password, User("x", "", "frontline", _DUMMY_SALT, _DUMMY_HASH))
            audit("login_failed", username, detail="unknown user")
            raise AuthError(generic)
        if not user.active:
            audit("login_failed", username, detail="account disabled")
            raise AuthError("This account is disabled. Contact your administrator.")
        if user.locked_until > time.time():
            mins = int((user.locked_until - time.time()) // 60) + 1
            audit("login_failed", username, detail="account locked")
            raise AuthError(f"Too many failed attempts. Try again in {mins} minute(s).")
        if not _check(password, user):
            user.fails += 1
            detail = f"wrong password ({user.fails})"
            if user.fails >= MAX_FAILS:
                user.locked_until = time.time() + LOCK_SECONDS
                user.fails = 0
                detail += ", account locked for 15 minutes"
            save_users(users)
            audit("login_failed", username, detail=detail)
            raise AuthError(generic)
        user.fails, user.locked_until, user.last_login = 0, 0.0, _now()
        save_users(users)
    audit("login", username)
    return user


def change_password(username: str, old: str, new: str) -> None:
    with _lock:
        users = load_users()
        user = users[username]
        if not _check(old, user):
            raise AuthError("The current password is wrong.")
        if old == new:
            raise AuthError("Choose a password different from the current one.")
        problem = password_problem(new, username)
        if problem:
            raise AuthError(problem)
        user.salt, user.pw_hash = hash_password(new)
        user.must_change = False
        save_users(users)
    audit("password_changed", username, by=username)


# ---------------------------------------------------------------- administration
def create_user(username: str, name: str, role: str, password: str | None = None, by: str = "",
                must_change: bool = True) -> str:
    """Create an account. Returns the initial password (generated if not given)."""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise AuthError("Username: 3–32 characters, lowercase letters, digits, dot, dash or underscore.")
    if role not in ROLES:
        raise AuthError(f"Unknown role '{role}'.")
    password = password or generate_password()
    problem = password_problem(password, username)
    if problem:
        raise AuthError(problem)
    with _lock:
        users = load_users()
        if username in users:
            raise AuthError(f"User '{username}' already exists.")
        salt, digest = hash_password(password)
        users[username] = User(username, name.strip() or username, role, salt, digest,
                               must_change=must_change, created=_now())
        save_users(users)
    audit("user_created", username, by=by, detail=f"role={role}")
    return password


def reset_password(username: str, by: str = "") -> str:
    """Set a new random temporary password; the user must change it at the next sign-in."""
    password = generate_password()
    with _lock:
        users = load_users()
        user = users[username]
        user.salt, user.pw_hash = hash_password(password)
        user.must_change, user.fails, user.locked_until = True, 0, 0.0
        save_users(users)
    audit("password_reset", username, by=by)
    return password


def update_user(username: str, by: str = "", role: str | None = None, active: bool | None = None) -> None:
    with _lock:
        users = load_users()
        user = users[username]
        if role is not None:
            if role not in ROLES:
                raise AuthError(f"Unknown role '{role}'.")
            if user.role == "admin" and role != "admin" and _admins(users) == 1:
                raise AuthError("Keep at least one administrator.")
            user.role = role
        if active is not None:
            if not active and user.role == "admin" and _admins(users) == 1:
                raise AuthError("Keep at least one active administrator.")
            user.active = active
            if active:
                user.fails, user.locked_until = 0, 0.0
        save_users(users)
    audit("user_updated", username, by=by,
          detail=", ".join(x for x in (f"role={role}" if role else "", f"active={active}" if active is not None else "") if x))


def _admins(users: dict[str, User]) -> int:
    return sum(1 for u in users.values() if u.role == "admin" and u.active)


def get_user(username: str) -> User | None:
    return load_users().get(username)


# ---------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m rag.auth", description="Manage Frontline Assistant accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="create an administrator (asks for a password)")
    p.add_argument("username")
    p.add_argument("--name", default="")
    p = sub.add_parser("add", help="add a user with a random temporary password")
    p.add_argument("username")
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--name", default="")
    p = sub.add_parser("reset", help="give a user a new temporary password")
    p.add_argument("username")
    sub.add_parser("list", help="list accounts")
    sub.add_parser("demo", help="create one demo account per role (random passwords, printed once)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "create-admin":
            pw = getpass.getpass("Password for the new administrator: ")
            if pw != getpass.getpass("Repeat the password: "):
                sys.exit("Passwords do not match.")
            create_user(args.username, args.name, "admin", pw, by="cli", must_change=False)
            print(f"Administrator '{args.username}' created.")
        elif args.cmd == "add":
            pw = create_user(args.username, args.name, args.role, by="cli")
            print(f"User '{args.username}' ({ROLES[args.role]['name']}) created.\n"
                  f"Temporary password: {pw}\n(They must change it at first sign-in.)")
        elif args.cmd == "reset":
            print(f"Temporary password for '{args.username}': {reset_password(args.username, by='cli')}")
        elif args.cmd == "list":
            for u in load_users().values():
                print(f"{u.username:<16} {ROLES[u.role]['name']:<20} {'active' if u.active else 'DISABLED':<9}"
                      f" last sign-in: {u.last_login or '—'}")
        elif args.cmd == "demo":
            print("Demo accounts (passwords are shown only now — note them down):\n")
            for role, info in ROLES.items():
                username = f"demo.{role}"
                if username in load_users():
                    print(f"  {username:<16} already exists (use 'reset' for a new password)")
                    continue
                pw = create_user(username, f"Demo {info['name']}", role, by="cli", must_change=False)
                print(f"  {username:<16} {pw}   {info['name']}")
            print("\nDelete or disable demo accounts before real use.")
    except (AuthError, KeyError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
