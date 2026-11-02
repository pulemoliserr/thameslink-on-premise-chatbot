"""Tests for accounts and sign-in.  Run:  python -m pytest -q"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import auth  # noqa: E402
from rag.usecases import BY_ID  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(auth, "DEMO_FILE", tmp_path / "demo_credentials.json")
    monkeypatch.delenv("DEMO_LOGINS", raising=False)


def test_password_is_not_stored_in_plain_text(tmp_path):
    auth.create_user("alice", "Alice", "revenue", "correct-horse-7")
    raw = auth.USERS_FILE.read_text()
    assert "correct-horse-7" not in raw and "pw_hash" in raw


def test_login_success_and_failure():
    auth.create_user("alice", "Alice", "revenue", "correct-horse-7")
    assert auth.authenticate("Alice", "correct-horse-7").role == "revenue"   # usernames are case-insensitive
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("alice", "wrong-password-1")
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("nobody", "whatever-123")       # same message: does not reveal which usernames exist


def test_lockout_after_five_failures():
    auth.create_user("bob", "Bob", "claims", "another-pass-9")
    for _ in range(auth.MAX_FAILS):
        with pytest.raises(auth.AuthError):
            auth.authenticate("bob", "nope-nope-1")
    with pytest.raises(auth.AuthError, match="Too many failed attempts"):
        auth.authenticate("bob", "another-pass-9")       # even the right password is refused while locked
    users = auth.load_users()
    users["bob"].locked_until = time.time() - 1
    auth.save_users(users)
    assert auth.authenticate("bob", "another-pass-9").username == "bob"


def test_disabled_account_cannot_sign_in():
    auth.create_user("admin1", "Admin", "admin", "admin-pass-12")
    auth.create_user("carol", "Carol", "assist", "blue-garden-34")
    auth.update_user("carol", active=False)
    with pytest.raises(auth.AuthError, match="disabled"):
        auth.authenticate("carol", "blue-garden-34")


def test_password_rules_and_change():
    assert auth.password_problem("short1") is not None
    assert auth.password_problem("onlyletterslong") is not None
    temp = auth.create_user("dave", "Dave", "station")
    assert auth.load_users()["dave"].must_change
    with pytest.raises(auth.AuthError):
        auth.change_password("dave", temp, "dave-is-here-1")      # contains the username
    auth.change_password("dave", temp, "new-secret-55")
    assert not auth.load_users()["dave"].must_change
    assert auth.authenticate("dave", "new-secret-55")


def test_last_admin_is_protected():
    auth.create_user("root1", "Root", "admin", "root-pass-123")
    with pytest.raises(auth.AuthError, match="administrator"):
        auth.update_user("root1", role="frontline")
    with pytest.raises(auth.AuthError, match="administrator"):
        auth.update_user("root1", active=False)


def test_roles_map_to_existing_use_cases():
    for role, info in auth.ROLES.items():
        assert info["use_cases"], role
        assert all(uc in BY_ID for uc in info["use_cases"]), role
    assert auth.ROLES["revenue"]["use_cases"][0] == "tickets"        # lands in their own area
    assert "claims" not in auth.ROLES["revenue"]["use_cases"]          # cannot open other groups' areas


def test_audit_log_has_no_passwords():
    auth.create_user("erin", "Erin", "frontline", "river-stone-77")
    auth.authenticate("erin", "river-stone-77")
    with pytest.raises(auth.AuthError):
        auth.authenticate("erin", "typo-stone-77")
    text = auth.AUDIT_FILE.read_text()
    assert "river-stone-77" not in text and "typo-stone-77" not in text
    assert [e["event"] for e in auth.read_audit()][:2] == ["login_failed", "login"]


def test_demo_accounts_listed_and_usable():
    assert auth.demo_logins() == []                    # nothing listed until demo accounts exist
    created = auth.create_demo_accounts()
    assert len(created) == len(auth.ROLES)
    listed = auth.demo_logins()
    assert [d["role"] for d in listed] == list(auth.ROLES)
    for d in listed:
        assert auth.authenticate(d["username"], d["password"]).role == d["role"]
    assert "pw_hash" not in auth.DEMO_FILE.read_text()


def test_demo_list_hidden_by_env(monkeypatch):
    auth.create_demo_accounts()
    monkeypatch.setenv("DEMO_LOGINS", "0")
    assert auth.demo_logins() == []


def test_demo_reset_gives_new_working_passwords():
    first = {u: pw for u, pw, _ in auth.create_demo_accounts()}
    again = auth.create_demo_accounts()                 # without --reset: unchanged
    assert all(status == "exists" and first[u] == pw for u, pw, status in again)
    second = {u: pw for u, pw, _ in auth.create_demo_accounts(reset=True)}
    assert all(first[u] != second[u] for u in first)
    assert auth.authenticate("demo.claims", second["demo.claims"]).role == "claims"


def test_demo_password_cannot_be_changed_and_reset_or_disable_unlists():
    auth.create_demo_accounts()
    pw = {d["username"]: d["password"] for d in auth.demo_logins()}
    with pytest.raises(auth.AuthError, match="Demo accounts"):
        auth.change_password("demo.revenue", pw["demo.revenue"], "a-new-pass-123")
    auth.reset_password("demo.revenue", by="demo.admin")
    auth.update_user("demo.assist", by="demo.admin", active=False)
    names = {d["username"] for d in auth.demo_logins()}
    assert "demo.revenue" not in names and "demo.assist" not in names and "demo.claims" in names
