"""Chat history (IT & data protection: "chats kept separate and not stored by default").

- By default a conversation lives only in the browser session (Streamlit session state): two
  sessions never see each other's chats, and nothing is written to disk.
- If IT allows it (settings) AND the member of staff switches it on for themselves, their own
  questions and answers are kept in STATE_DIR/history/<username>.jsonl. Only that user can read
  it (there is no function to read anyone else's), and they can delete it at any time.
- Old entries are deleted after the retention period; answers that cite a removed document are
  deleted when the document is removed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, settings


def _dir() -> Path:
    return config.STATE_DIR / "history"


def _file(username: str) -> Path:
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise ValueError("bad username")
    return _dir() / f"{username}.jsonl"


def enabled_for(user) -> bool:
    return bool(settings.load()["allow_chat_history"] and user.keep_history)


def save(user, question: str, answer_text: str, sources: list[dict]) -> bool:
    """Keep one question and answer, if the user chose to. Returns True if it was stored."""
    if not enabled_for(user):
        return False
    _dir().mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "question": question,
             "answer": answer_text,
             "sources": [{"doc_id": s["doc_id"], "label": s["label"], "pages": s["pages"], "doc_short": s["doc_short"]}
                         for s in sources]}
    with _file(user.username).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def load_own(user) -> list[dict]:
    """The signed-in user's own kept history (newest first)."""
    path = _file(user.username)
    if not path.exists():
        return []
    return [json.loads(l) for l in reversed(path.read_text(encoding="utf-8").splitlines()) if l.strip()]


def clear_own(user) -> None:
    _file(user.username).unlink(missing_ok=True)


def _rewrite(path: Path, keep) -> int:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    kept = [l for l in lines if keep(json.loads(l))]
    if kept:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        path.unlink()
    return len(lines) - len(kept)


def forget_document(doc_id: str) -> int:
    if not _dir().exists():
        return 0
    return sum(_rewrite(p, lambda e: all(s["doc_id"] != doc_id for s in e["sources"]))
               for p in _dir().glob("*.jsonl"))


def mentions_document(doc_id: str) -> bool:
    if not _dir().exists():
        return False
    return any(any(s["doc_id"] == doc_id for s in json.loads(l)["sources"])
               for p in _dir().glob("*.jsonl") for l in p.read_text(encoding="utf-8").splitlines() if l.strip())


def purge(cutoff: datetime) -> int:
    if not _dir().exists():
        return 0
    return sum(_rewrite(p, lambda e: datetime.fromisoformat(e["time"]) >= cutoff) for p in _dir().glob("*.jsonl"))
