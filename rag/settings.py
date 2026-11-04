"""Settings IT & data protection can change in the app (stored in STATE_DIR/settings.json).

Every change is written to the audit log.
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
    auth.require(actor, "manage_settings")
    by = actor if isinstance(actor, str) else actor.username
    current = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting {key}")
        value = type(DEFAULTS[key])(value)
        if isinstance(value, int) and not isinstance(value, bool) and value < 1:
            raise ValueError(f"{LABELS[key]} must be at least 1.")
        if current[key] != value:
            auth.audit("setting_changed", key, by=by, detail=f"{current[key]} → {value}")
            current[key] = value
    _file().write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current
