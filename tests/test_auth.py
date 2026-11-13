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
    auth.create_user("alice", "Alice", "conductor", "correct-horse-7", by="cli")
    raw = auth.USERS_FILE.read_text()
    assert "correct-horse-7" not in raw and "pw_hash" in raw


def test_login_success_and_failure():
    auth.create_user("alice", "Alice", "conductor", "correct-horse-7", by="cli")
    assert auth.authenticate("Alice", "correct-horse-7").role == "conductor"   # usernames are case-insensitive
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("alice", "wrong-password-1")
    with pytest.raises(auth.AuthError, match="Wrong username or password"):
        auth.authenticate("nobody", "whatever-123")       # same message: does not reveal which usernames exist


def test_lockout_after_five_failures():
    auth.create_user("bob", "Bob", "revenue", "another-pass-9", by="cli")
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
    auth.create_user("admin1", "Admin", "it_ops", "admin-pass-12", by="cli")
    auth.create_user("carol", "Carol", "station_staff", "blue-garden-34", by="admin1")
    auth.update_user("carol", by="admin1", active=False)
    with pytest.raises(auth.AuthError, match="disabled"):
        auth.authenticate("carol", "blue-garden-34")


def test_password_rules_and_change():
    assert auth.password_problem("short1") is not None
    assert auth.password_problem("onlyletterslong") is not None
    temp = auth.create_user("dave", "Dave", "duty_manager", by="cli")
    assert auth.load_users()["dave"].must_change
    with pytest.raises(auth.AuthError):
        auth.change_password("dave", temp, "dave-is-here-1")      # contains the username
    auth.change_password("dave", temp, "new-secret-55")
    assert not auth.load_users()["dave"].must_change
    assert auth.authenticate("dave", "new-secret-55")


def test_last_it_admin_is_protected():
    auth.create_user("root1", "Root", "it_ops", "root-pass-123", by="cli")
    with pytest.raises(auth.AuthError, match="IT administrator"):
        auth.update_user("root1", by="cli", role="conductor")
    with pytest.raises(auth.AuthError, match="IT administrator"):
        auth.update_user("root1", by="cli", active=False)


PRESENTATION_USERS = {   # slide 6 / 7 user groups -> role
    "station teams": "station_staff", "station duty manager": "duty_manager", "conductors": "conductor",
    "revenue protection": "revenue", "customer relations / customer service agent": "customer_relations",
    "policy owner": "policy_owner", "IT operations": "it_ops", "data protection lead": "data_protection"}


def test_every_user_group_from_the_presentation_has_a_role():
    assert set(PRESENTATION_USERS.values()) == set(auth.ROLES)
    for role, info in auth.ROLES.items():
        assert info["name"] and info["group"] and info["story"] and info["landing"], role


def test_role_permissions():
    def u(role, **kw):
        return auth.User("x", "", role, "", "", **kw)
    for role in ("station_staff", "duty_manager", "conductor", "revenue", "customer_relations"):
        assert u(role).can("chat") and u(role).can("advisors")
        assert not any(u(role).can(p) for p in ("inspect_answers", "manage_users", "edit_documents", "manage_privacy"))
        assert auth.ROLES[role]["examples"] and auth.ROLES[role]["checks"]
    assert u("policy_owner").can("inspect_answers") and not u("policy_owner").can("edit_documents")
    assert u("policy_owner", doc_editor=True).can("edit_documents")
    assert not u("conductor", doc_editor=True).can("edit_documents")             # the flag alone is not enough
    assert u("it_ops").can("manage_users") and u("it_ops").can("manage_backups")
    assert not u("it_ops").can("manage_privacy") and not u("it_ops").can("chat")
    assert u("data_protection").can("manage_privacy") and u("data_protection").can("verify_deletion")
    assert not u("data_protection").can("manage_users") and not u("data_protection").can("manage_backups")


def test_each_frontline_role_lands_on_its_user_story():
    assert auth.ROLES["conductor"]["topic"] == "tickets" and auth.ROLES["conductor"]["landing"] == "checks"
    assert auth.ROLES["duty_manager"]["topic"] == "claims"
    assert auth.ROLES["station_staff"]["topic"] == "assist"
    assert auth.ROLES["revenue"]["checks"] == ["tickets"]


def test_version1_accounts_are_migrated():
    auth.create_user("old1", "Old", "conductor", "old-account-77", by="cli")
    for old, new in (("frontline", "station_staff"), ("claims", "customer_relations"), ("admin", "it_ops"),
                     ("staff", "customer_relations"), ("it", "it_ops")):
        users = auth.load_users()
        users["old1"].role = old
        auth.save_users(users)
        assert auth.load_users()["old1"].role == new


def test_only_editors_name_editors():
    auth.create_user("it1", "IT", "it_ops", "it-pass-1234", by="cli")
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
    auth.create_user("erin", "Erin", "customer_relations", "river-stone-77", by="cli")
    auth.authenticate("erin", "river-stone-77")
    with pytest.raises(auth.AuthError):
        auth.authenticate("erin", "typo-stone-77")
    text = auth.AUDIT_FILE.read_text()
    assert "river-stone-77" not in text and "typo-stone-77" not in text
    assert [e["event"] for e in auth.read_audit()][:2] == ["login_failed", "login"]


def test_demo_accounts_listed_and_usable():
    assert auth.demo_logins() == []                    # nothing listed until demo accounts exist
    created = auth.create_demo_accounts()
    assert len(created) == 8
    listed = auth.demo_logins()
    assert [d["role"] for d in listed] == list(auth.ROLES)
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
    assert auth.authenticate("demo.conductor", second["demo.conductor"]).role == "conductor"


def test_demo_password_cannot_be_changed_and_reset_or_disable_unlists():
    auth.create_demo_accounts()
    pw = {d["username"]: d["password"] for d in auth.demo_logins()}
    with pytest.raises(auth.AuthError, match="Demo accounts"):
        auth.change_password("demo.station", pw["demo.station"], "a-new-pass-123")
    auth.reset_password("demo.station", by="demo.itops")
    names = {d["username"] for d in auth.demo_logins()}
    assert "demo.station" not in names and "demo.itops" in names


def test_public_demo_accounts_cannot_be_reset_or_disabled(monkeypatch):
    from rag import config
    monkeypatch.setattr(config, "DEMO_MODE", True)
    auth.create_demo_accounts()
    with pytest.raises(auth.AuthError, match="shared demo account"):
        auth.reset_password("demo.station", by="demo.itops")
    with pytest.raises(auth.AuthError, match="shared demo account"):
        auth.update_user("demo.policy", by="demo.itops", active=False)
    assert len(auth.demo_logins()) == 8
