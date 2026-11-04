"""Tests for accounts and sign-in.  Run:  python -m pytest -q"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import auth  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(auth, "DEMO_FILE", tmp_path / "demo_credentials.json")
    monkeypatch.delenv("DEMO_LOGINS", raising=False)


def test_password_is_not_stored_in_plain_text(tmp_path):
    auth.create_user("alice", "Alice", "staff", "correct-horse-7", by="cli")
    raw = auth.USERS_FILE.read_text()
    assert "correct-horse-7" not in raw and "pw_hash" in raw


def test_login_success_and_failure():
    auth.create_user("alice", "Alice", "staff", "correct-horse-7", by="cli")
    assert auth.authenticate("Alice", "correct-horse-7").role == "staff"   # usernames are case-insensitive
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("alice", "wrong-password-1")
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("nobody", "whatever-123")       # same message: does not reveal which usernames exist


def test_lockout_after_five_failures():
    auth.create_user("bob", "Bob", "staff", "another-pass-9", by="cli")
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
    auth.create_user("admin1", "Admin", "it", "admin-pass-12", by="cli")
    auth.create_user("carol", "Carol", "staff", "blue-garden-34", by="admin1")
    auth.update_user("carol", by="admin1", active=False)
    with pytest.raises(auth.AuthError, match="disabled"):
        auth.authenticate("carol", "blue-garden-34")


def test_password_rules_and_change():
    assert auth.password_problem("short1") is not None
    assert auth.password_problem("onlyletterslong") is not None
    temp = auth.create_user("dave", "Dave", "staff", by="cli")
    assert auth.load_users()["dave"].must_change
    with pytest.raises(auth.AuthError):
        auth.change_password("dave", temp, "dave-is-here-1")      # contains the username
    auth.change_password("dave", temp, "new-secret-55")
    assert not auth.load_users()["dave"].must_change
    assert auth.authenticate("dave", "new-secret-55")


def test_last_it_admin_is_protected():
    auth.create_user("root1", "Root", "it", "root-pass-123", by="cli")
    with pytest.raises(auth.AuthError, match="IT administrator"):
        auth.update_user("root1", by="cli", role="staff")
    with pytest.raises(auth.AuthError, match="IT administrator"):
        auth.update_user("root1", by="cli", active=False)


def test_three_roles_and_their_permissions():
    assert set(auth.ROLES) == {"staff", "policy_owner", "it"}
    staff = auth.User("s", "", "staff", "", "")
    owner = auth.User("p", "", "policy_owner", "", "")
    editor = auth.User("e", "", "policy_owner", "", "", doc_editor=True)
    it = auth.User("i", "", "it", "", "")
    assert staff.can("chat") and not staff.can("inspect_answers") and not staff.can("manage_users")
    assert owner.can("inspect_answers") and not owner.can("edit_documents")     # editing needs the editor right
    assert editor.can("edit_documents") and editor.can("manage_editors")
    assert it.can("manage_users") and not it.can("edit_documents") and not it.can("chat")
    assert not auth.User("x", "", "staff", "", "", doc_editor=True).can("edit_documents")   # flag alone is not enough


def test_version1_accounts_are_migrated():
    auth.create_user("old1", "Old", "staff", "old-account-77", by="cli")
    raw = auth.USERS_FILE.read_text().replace('"role": "staff"', '"role": "revenue"')
    auth.USERS_FILE.write_text(raw)
    assert auth.load_users()["old1"].role == "staff"


def test_only_editors_name_editors():
    auth.create_user("it1", "IT", "it", "it-pass-1234", by="cli")
    auth.create_user("pol1", "Pol", "policy_owner", "pol-pass-1234", by="cli", doc_editor=True)
    auth.create_user("pol2", "Pol Two", "policy_owner", "pol-pass-5678", by="it1")
    assert not auth.get_user("pol2").doc_editor
    with pytest.raises(auth.PermissionDenied):
        auth.create_user("pol3", "P3", "policy_owner", "pol-pass-9999", by="it1", doc_editor=True)
    with pytest.raises(auth.PermissionDenied):
        auth.set_doc_editor("pol2", True, by="it1")             # IT cannot give document rights
    auth.set_doc_editor("pol2", True, by="pol1")
    assert auth.get_user("pol2").can("edit_documents")
    auth.set_doc_editor("pol1", False, by="pol2")
    with pytest.raises(auth.AuthError, match="at least one"):
        auth.set_doc_editor("pol2", False, by="pol2")           # keep one editor
    events = [e["event"] for e in auth.read_audit()]
    assert "document_editor_named" in events and "document_editor_removed" in events


def test_audit_log_has_no_passwords():
    auth.create_user("erin", "Erin", "staff", "river-stone-77", by="cli")
    auth.authenticate("erin", "river-stone-77")
    with pytest.raises(auth.AuthError):
        auth.authenticate("erin", "typo-stone-77")
    text = auth.AUDIT_FILE.read_text()
    assert "river-stone-77" not in text and "typo-stone-77" not in text
    assert [e["event"] for e in auth.read_audit()][:2] == ["login_failed", "login"]


def test_demo_accounts_listed_and_usable():
    assert auth.demo_logins() == []                    # nothing listed until demo accounts exist
    created = auth.create_demo_accounts()
    assert len(created) == 3
    listed = auth.demo_logins()
    assert [d["role"] for d in listed] == ["staff", "policy_owner", "it"]
    for d in listed:
        assert auth.authenticate(d["username"], d["password"]).role == d["role"]
    assert auth.get_user("demo.policy").doc_editor
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
    assert auth.authenticate("demo.staff", second["demo.staff"]).role == "staff"


def test_demo_password_cannot_be_changed_and_reset_or_disable_unlists():
    auth.create_demo_accounts()
    pw = {d["username"]: d["password"] for d in auth.demo_logins()}
    with pytest.raises(auth.AuthError, match="Demo accounts"):
        auth.change_password("demo.staff", pw["demo.staff"], "a-new-pass-123")
    auth.reset_password("demo.staff", by="demo.it")
    names = {d["username"] for d in auth.demo_logins()}
    assert "demo.staff" not in names and "demo.it" in names


def test_public_demo_accounts_cannot_be_reset_or_disabled(monkeypatch):
    from rag import config
    monkeypatch.setattr(config, "DEMO_MODE", True)
    auth.create_demo_accounts()
    with pytest.raises(auth.AuthError, match="shared demo account"):
        auth.reset_password("demo.staff", by="demo.it")
    with pytest.raises(auth.AuthError, match="shared demo account"):
        auth.update_user("demo.policy", by="demo.it", active=False)
    assert len(auth.demo_logins()) == 3
