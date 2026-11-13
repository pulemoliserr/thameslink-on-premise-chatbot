"""Settings changed in the app (stored in STATE_DIR/settings.json). Every change is written to the audit log.

Privacy settings (retention, chat history) belong to the data protection lead; the response-time
target belongs to IT operations.
"""
from __future__ import annotations

import json

from . import auth, config

DEFAULTS = {
    "response_target_seconds": 10,      # agreed response time for an answer (shown on the IT dashboard)
    "usage_log_days": 30,               # technical request log (no question text)
    "feedback_days": 90,                # answer ratings
    "audit_log_days": 365,              # sign-ins, account and document changes
    "allow_chat_history": False,        # may staff choose to keep their chat history on the server?
    "chat_history_days": 30,            # how long kept chat histories are stored
}
# which permission may change each setting
OWNER = {"response_target_seconds": "manage_operations", "usage_log_days": "manage_privacy",
         "feedback_days": "manage_privacy", "audit_log_days": "manage_privacy", "allow_chat_history": "manage_privacy",
         "chat_history_days": "manage_privacy"}
LABELS = {
    "response_target_seconds": "Agreed response time (seconds)",
    "usage_log_days": "Keep the technical request log for (days)",
    "feedback_days": "Keep answer ratings for (days)",
    "audit_log_days": "Keep the audit log for (days)",
    "allow_chat_history": "Staff may choose to keep their chat history",
    "chat_history_days": "Keep chosen chat histories for (days)",
}


def _file():
    return config.STATE_DIR / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    if _file().exists():
        try:
            stored = json.loads(_file().read_text(encoding="utf-8"))
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except ValueError:
            pass
    return data


def update(actor, **changes) -> dict:
    """Change settings. Every changed setting needs its owner's permission (checked before anything is saved)."""
    by = actor if isinstance(actor, str) else actor.username
    current = load()
    changed = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting {key}")
        value = type(DEFAULTS[key])(value)
        if isinstance(value, int) and not isinstance(value, bool) and value < 1:
            raise ValueError(f"{LABELS[key]} must be at least 1.")
        if value != current[key]:
            changed[key] = value
    for key in changed:
        auth.require(actor, OWNER[key])
    for key, value in changed.items():
        auth.audit("setting_changed", key, by=by, detail=f"{current[key]} → {value}")
        current[key] = value
    _file().write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current
