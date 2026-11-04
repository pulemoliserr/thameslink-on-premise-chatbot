"""Accounts, roles and sign-in for the Frontline Assistant.

- Passwords are stored only as salted scrypt hashes (Python standard library), never in plain text.
- Each user has one of three roles (staff, policy owner, IT & data protection). The role decides
  the permissions; every admin-only action checks them on the server (`require`), not only the menu.
- Changing documents needs the policy-owner role AND the named "document editor" right, which
  only existing document editors can grant (separation of duties: IT manages accounts, the
  policy owner decides who may change documents).
- After 5 failed sign-ins an account is locked for 15 minutes.
- Sign-ins, sign-outs and account changes are written to an audit log (no passwords).

Accounts live in data/users.json on the server. Create them from the command line:

    python -m rag.auth create-admin itlead
    python -m rag.auth add jsmith --role staff --name "Jo Smith"
    python -m rag.auth add pbrown --role policy_owner --editor     # may change documents
    python -m rag.auth demo            # one account per role; passwords shown on the sign-in page
    python -m rag.auth demo --reset    # new passwords for the existing demo accounts
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
# Plain-text passwords of the DEMO accounts only, so the sign-in page can list them for testers.
# Never committed (see .gitignore). Delete this file, or set DEMO_LOGINS=0, to hide the list.
DEMO_FILE = config.STATE_DIR / "demo_credentials.json"

MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
SESSION_IDLE_SECONDS = 30 * 60
MIN_PASSWORD_LEN = 10

# role id -> display name, who it is for, permissions
ROLES: dict[str, dict] = {
    "staff": {"name": "Staff & customer service",
              "group": "Station teams, conductors, revenue protection, customer relations",
              "perms": {"chat", "advisors", "view_documents"}},
    "policy_owner": {"name": "Policy owner", "group": "Owner of the policy documents",
                     "perms": {"chat", "advisors", "view_documents", "inspect_answers", "manage_testset",
                               "view_document_history", "view_feedback"}},
    "it": {"name": "IT & data protection", "group": "IT operations, data protection lead",
           "perms": {"manage_users", "view_audit", "view_usage", "manage_settings",
                     "manage_backups", "system"}},
}
# Rights a policy owner only has when named a document editor.
EDITOR_PERMS = {"edit_documents", "manage_editors"}
OLD_ROLES = {"frontline": "staff", "revenue": "staff", "assist": "staff", "claims": "staff",
             "station": "staff", "admin": "it"}           # accounts from version 1

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
    doc_editor: bool = False          # named document editor (policy owners only)
    keep_history: bool = False        # agreed to keep their chat history on the server

    @property
    def perms(self) -> set[str]:
        perms = set(ROLES[self.role]["perms"])
        if self.role == "policy_owner" and self.doc_editor:
            perms |= EDITOR_PERMS
        return perms

    def can(self, perm: str) -> bool:
        return self.active and perm in self.perms


class AuthError(Exception):
    pass


class PermissionDenied(AuthError):
    pass


def require(actor: "User | str | None", perm: str) -> None:
    """Server-side permission check. `actor` is a User, a username, or "cli" (the server's own command line,
    which only IT can reach). Raises PermissionDenied and writes it to the audit log."""
    if actor == "cli":
        return
    user = actor if isinstance(actor, User) else (get_user(actor) if actor else None)
    if user is None or not user.can(perm):
        name = user.username if user else str(actor or "anonymous")
        audit("permission_denied", name, by=name, detail=perm)
        raise PermissionDenied("You do not have permission to do this.")


# ---------------------------------------------------------------- storage
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_users() -> dict[str, User]:
    if not USERS_FILE.exists():
        return {}
    data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    users = {}
    for u in data["users"]:
        u = {k: v for k, v in u.items() if k in User.__dataclass_fields__}
        u["role"] = OLD_ROLES.get(u["role"], u["role"])
        users[u["username"]] = User(**u)
    return users


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
            _check(password, User("x", "", "staff", _DUMMY_SALT, _DUMMY_HASH))
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
    if is_demo(username):
        raise AuthError("Demo accounts are shared, so their password can't be changed here.")
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
                must_change: bool = True, doc_editor: bool = False) -> str:
    """Create an account. Returns the initial password (generated if not given).
    `by`: the IT user creating it, or "cli". Only the command line can create a document editor directly."""
    require(by, "manage_users")
    if doc_editor and by != "cli":
        raise PermissionDenied("Only a document editor can name another document editor.")
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
                               must_change=must_change, created=_now(),
                               doc_editor=doc_editor and role == "policy_owner")
        save_users(users)
    audit("user_created", username, by=by, detail=f"role={role}")
    return password


def _demo_locked(username: str) -> None:
    if config.DEMO_MODE and is_demo(username):
        raise AuthError("This is a shared demo account, so it can't be changed in the public demo.")


def reset_password(username: str, by: str = "") -> str:
    """Set a new random temporary password; the user must change it at the next sign-in."""
    require(by, "manage_users")
    _demo_locked(username)
    password = generate_password()
    with _lock:
        users = load_users()
        user = users[username]
        user.salt, user.pw_hash = hash_password(password)
        user.must_change, user.fails, user.locked_until = True, 0, 0.0
        save_users(users)
    forget_demo(username)
    audit("password_reset", username, by=by)
    return password


def update_user(username: str, by: str = "", role: str | None = None, active: bool | None = None) -> None:
    require(by, "manage_users")
    _demo_locked(username)
    with _lock:
        users = load_users()
        user = users[username]
        if role is not None:
            if role not in ROLES:
                raise AuthError(f"Unknown role '{role}'.")
            if user.role == "it" and role != "it" and _admins(users) == 1:
                raise AuthError("Keep at least one IT administrator.")
            if role != "policy_owner" and user.doc_editor:
                if _editors(users) == 1:
                    raise AuthError("This is the last document editor. Name another editor first.")
                user.doc_editor = False
            user.role = role
        if active is not None:
            if not active and user.role == "it" and _admins(users) == 1:
                raise AuthError("Keep at least one active IT administrator.")
            if not active and user.doc_editor and _editors(users) == 1:
                raise AuthError("This is the last document editor. Name another editor first.")
            user.active = active
            if active:
                user.fails, user.locked_until = 0, 0.0
        save_users(users)
    audit("user_updated", username, by=by,
          detail=", ".join(x for x in (f"role={role}" if role else "", f"active={active}" if active is not None else "") if x))


def _admins(users: dict[str, User]) -> int:
    return sum(1 for u in users.values() if u.role == "it" and u.active)


def _editors(users: dict[str, User]) -> int:
    return sum(1 for u in users.values() if u.role == "policy_owner" and u.doc_editor and u.active)


def set_doc_editor(username: str, editor: bool, by: str) -> None:
    """Name (or remove) a document editor. Only document editors can do this, and only for policy owners."""
    require(by, "manage_editors")
    with _lock:
        users = load_users()
        user = users[username]
        if editor and user.role != "policy_owner":
            raise AuthError("Only policy owners can be document editors.")
        if not editor and user.doc_editor and _editors(users) == 1:
            raise AuthError("Keep at least one document editor.")
        user.doc_editor = editor
        save_users(users)
    audit("document_editor_" + ("named" if editor else "removed"), username, by=by)


def set_keep_history(username: str, keep: bool) -> None:
    """A user's own choice to keep their chat history on the server (opt-in)."""
    with _lock:
        users = load_users()
        users[username].keep_history = keep
        save_users(users)
    audit("chat_history_" + ("on" if keep else "off"), username, by=username)


def get_user(username: str) -> User | None:
    return load_users().get(username)


# ---------------------------------------------------------------- demo accounts
def demo_logins() -> list[dict]:
    """Demo accounts to list on the sign-in page: [{username, password, role, name}], active ones only."""
    if os.getenv("DEMO_LOGINS", "1").strip().lower() in ("0", "false", "no", "off") or not DEMO_FILE.exists():
        return []
    try:
        saved = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    users = load_users()
    out = []
    for username, password in saved.items():
        u = users.get(username)
        if u and u.active:
            out.append({"username": username, "password": password, "role": u.role, "name": ROLES[u.role]["name"]})
    order = list(ROLES)
    return sorted(out, key=lambda d: order.index(d["role"]) if d["role"] in order else 99)


def _save_demo(saved: dict[str, str]) -> None:
    DEMO_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not saved:
        DEMO_FILE.unlink(missing_ok=True)
        return
    tmp = DEMO_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    os.replace(tmp, DEMO_FILE)
    try:
        os.chmod(DEMO_FILE, 0o600)
    except OSError:
        pass


def _load_demo() -> dict[str, str]:
    try:
        return json.loads(DEMO_FILE.read_text(encoding="utf-8")) if DEMO_FILE.exists() else {}
    except (OSError, ValueError):
        return {}


def is_demo(username: str) -> bool:
    return username in _load_demo()


def forget_demo(username: str) -> None:
    """Stop listing an account on the sign-in page (its password is no longer the published one)."""
    with _lock:
        saved = _load_demo()
        if saved.pop(username, None) is not None:
            _save_demo(saved)


DEMO_USERNAMES = {"staff": "demo.staff", "policy_owner": "demo.policy", "it": "demo.it"}


def create_demo_accounts(reset: bool = False) -> list[tuple[str, str, str]]:
    """One demo account per role. Returns (username, password or '', status) per role."""
    results = []
    with _lock:
        saved = _load_demo()
        for role, info in ROLES.items():
            username = DEMO_USERNAMES[role]
            if username in load_users():
                if not reset:
                    results.append((username, saved.get(username, ""), "exists"))
                    continue
                pw = generate_password()
                users = load_users()
                u = users[username]
                u.salt, u.pw_hash = hash_password(pw)
                u.must_change, u.fails, u.locked_until, u.active = False, 0, 0.0, True
                save_users(users)
                audit("password_reset", username, by="cli", detail="demo")
                status = "new password"
            else:
                pw = create_user(username, f"Demo {info['name']}", role, by="cli", must_change=False,
                                 doc_editor=(role == "policy_owner"))
                status = "created"
            saved[username] = pw
            results.append((username, pw, status))
        _save_demo(saved)
    return results


# ---------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m rag.auth", description="Manage Frontline Assistant accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="create an IT administrator (asks for a password)")
    p.add_argument("username")
    p.add_argument("--name", default="")
    p = sub.add_parser("add", help="add a user with a random temporary password")
    p.add_argument("username")
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--name", default="")
    p.add_argument("--editor", action="store_true", help="policy owner who may change documents")
    p = sub.add_parser("reset", help="give a user a new temporary password")
    p.add_argument("username")
    sub.add_parser("list", help="list accounts")
    p = sub.add_parser("demo", help="create one demo account per role (passwords listed on the sign-in page)")
    p.add_argument("--reset", action="store_true", help="give the existing demo accounts new passwords")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "create-admin":
            pw = getpass.getpass("Password for the new administrator: ")
            if pw != getpass.getpass("Repeat the password: "):
                sys.exit("Passwords do not match.")
            create_user(args.username, args.name, "it", pw, by="cli", must_change=False)
            print(f"IT administrator '{args.username}' created.")
        elif args.cmd == "add":
            pw = create_user(args.username, args.name, args.role, by="cli", doc_editor=args.editor)
            print(f"User '{args.username}' ({ROLES[args.role]['name']}) created.\n"
                  f"Temporary password: {pw}\n(They must change it at first sign-in.)")
        elif args.cmd == "reset":
            print(f"Temporary password for '{args.username}': {reset_password(args.username, by='cli')}")
        elif args.cmd == "list":
            for u in load_users().values():
                print(f"{u.username:<16} {ROLES[u.role]['name']:<26} {'editor' if u.doc_editor else '':<7}"
                      f"{'active' if u.active else 'DISABLED':<9}"
                      f" last sign-in: {u.last_login or '—'}")
        elif args.cmd == "demo":
            print("Demo accounts:\n")
            for username, pw, status in create_demo_accounts(reset=args.reset):
                shown = pw or "(password not saved: run with --reset)"
                print(f"  {username:<16} {shown:<16} {status}")
            print(f"\nThe sign-in page lists these accounts (saved in {DEMO_FILE}).\n"
                  "Hide the list with DEMO_LOGINS=0 or by deleting that file. Remove demo accounts before real use.")
    except (AuthError, KeyError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
