"""Minimal logs (IT & data protection: "logs with only what support needs").

- usage.jsonl: one line per answer with role, topic, answer method, found / not found, seconds and
  an error code. NO usernames, question text or answer text.
- feedback.jsonl: 👍/👎 with the passage ids used. The question and answer are only stored when the
  member of staff ticks "share my question" (so the policy owner can check the answer).
- `purge` deletes entries older than the retention set by IT (settings), including the audit log
  and kept chat histories.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import auth, config, settings


def usage_file() -> Path:
    return config.STATE_DIR / "usage.jsonl"


def feedback_file() -> Path:
    return config.STATE_DIR / "feedback.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if limit:
        lines = lines[-limit:]
    return [json.loads(l) for l in reversed(lines)]


def log_request(role: str, topic: str, generator: str, found: bool, seconds: float, n_sources: int,
                error: str = "") -> None:
    _append(usage_file(), {"time": _now(), "role": role, "topic": topic,
                           "method": "language model" if generator.startswith("llm") else generator,
                           "found": found, "seconds": round(seconds, 2), "sources": n_sources, "error": error})


def read_usage(limit: int = 1000) -> list[dict]:
    return _read(usage_file(), limit)


def usage_stats(entries: list[dict] | None = None) -> dict:
    entries = read_usage() if entries is None else entries
    secs = sorted(e["seconds"] for e in entries)
    target = settings.load()["response_target_seconds"]
    if not secs:
        return {"requests": 0, "median": 0.0, "p95": 0.0, "within_target": 1.0, "not_found": 0.0, "errors": 0,
                "target": target}
    p95 = secs[min(len(secs) - 1, int(round(0.95 * (len(secs) - 1))))]
    return {"requests": len(secs), "median": statistics.median(secs), "p95": p95,
            "within_target": sum(s <= target for s in secs) / len(secs),
            "not_found": sum(not e["found"] for e in entries) / len(entries),
            "errors": sum(bool(e.get("error")) for e in entries), "target": target}


def log_feedback(role: str, rating: str, generator: str, source_ids: list[str],
                 question: str | None = None, answer: str | None = None) -> None:
    entry = {"time": _now(), "role": role, "rating": rating, "generator": generator, "sources": source_ids}
    if question is not None:                 # only with the member of staff's consent
        entry.update({"question": question, "answer": answer or ""})
    _append(feedback_file(), entry)


def read_feedback(actor, limit: int = 500) -> list[dict]:
    auth.require(actor, "view_feedback")
    return _read(feedback_file(), limit)


def purge(now: datetime | None = None) -> dict:
    """Delete log entries older than their retention period. Returns how many entries were removed."""
    now = now or datetime.now(timezone.utc)
    s = settings.load()
    removed = {}
    for path, days in ((usage_file(), s["usage_log_days"]), (feedback_file(), s["feedback_days"]),
                       (auth.AUDIT_FILE, s["audit_log_days"])):
        removed[path.name] = _purge_file(path, now - timedelta(days=days))
    from . import history
    removed["chat histories"] = history.purge(now - timedelta(days=s["chat_history_days"]))
    return removed


def _purge_file(path: Path, cutoff: datetime) -> int:
    if not path.exists():
        return 0
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    keep = [l for l in lines if datetime.fromisoformat(json.loads(l)["time"]) >= cutoff]
    if len(keep) != len(lines):
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return len(lines) - len(keep)
