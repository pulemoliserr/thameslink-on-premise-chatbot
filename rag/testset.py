"""The test set (policy owner: "the test set is rerun after every change").

Questions live in STATE_DIR/testset.yaml (first copied from eval/questions.yaml), so the policy
owner can add real staff questions in the app. Each run is stored in STATE_DIR/testset_runs.jsonl.

Outcome per question
- answerable question: "correct" (answers and mentions the required facts), "wrong" (answers, but a
  required fact is missing), or "not found" (says it can't find it: safe, but a gap to fix);
- out-of-scope question: "correct" when it says "not found", "wrong" when it answers anyway.
Target agreed in the status meeting: no wrong answers.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import auth, config
from .answer import NOT_FOUND, answer

SEED = config.ROOT / "eval" / "questions.yaml"


def testset_file() -> Path:
    return config.STATE_DIR / "testset.yaml"


def runs_file() -> Path:
    return config.STATE_DIR / "testset_runs.jsonl"


def load() -> list[dict]:
    path = testset_file() if testset_file().exists() else SEED
    qs = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    for q in qs:
        q.setdefault("answerable", True)
        q.setdefault("docs", [])
        q.setdefault("facts", [])
        q.setdefault("evidence", "")
    return qs


def save(actor, questions: list[dict]) -> None:
    auth.require(actor, "manage_testset")
    by = actor if isinstance(actor, str) else actor.username
    seen, clean = set(), []
    for i, q in enumerate(questions, start=1):
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        qid = str(q.get("id") or "").strip() or f"q{i:02d}"
        while qid in seen:
            qid += "b"
        seen.add(qid)
        facts = q.get("facts") or []
        if isinstance(facts, str):
            facts = [f.strip() for f in facts.split(";") if f.strip()]
        docs = q.get("docs") or []
        if isinstance(docs, str):
            docs = [d.strip() for d in docs.split(",") if d.strip()]
        for pattern in facts + ([q["evidence"]] if q.get("evidence") else []):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Question {qid}: “{pattern}” is not a valid pattern ({e}).") from e
        clean.append({"id": qid, "question": text, "answerable": bool(q.get("answerable", True)),
                      "category": q.get("category", ""), "docs": docs, "facts": facts,
                      "evidence": q.get("evidence", "") or "", "ref": q.get("ref", "") or ""})
    testset_file().write_text(yaml.safe_dump({"questions": clean}, sort_keys=False, allow_unicode=True),
                              encoding="utf-8")
    auth.audit("testset_changed", "", by=by, detail=f"{len(clean)} questions")


def judge(q: dict, a) -> dict:
    refused = (not a.found) or a.text.startswith(NOT_FOUND[:20])
    body = a.text
    if q["answerable"]:
        if refused:
            outcome = "not found"
        elif all(re.search(f, body, re.I) for f in q["facts"]):
            outcome = "correct"
        else:
            outcome = "wrong"
    else:
        outcome = "correct" if refused else "wrong"
    cited = [s for s in a.sources if s.cited] or a.sources[:1]
    right_source = None
    if q["answerable"] and not refused:
        right_source = any((not q["docs"] or s.doc_id in q["docs"])
                           and (not q["evidence"] or re.search(q["evidence"], s.text, re.I)) for s in cited)
    return {"id": q["id"], "question": q["question"], "answerable": q["answerable"], "outcome": outcome,
            "right_source": right_source, "answer": a.text[:600],
            "sources": [f"{s.doc_short} — {s.label} ({s.pages})" for s in cited]}


def run(retriever, trigger: str, by: str = "", use_llm: bool = False, questions: list[dict] | None = None) -> dict:
    """Run every question. Returns and stores {time, trigger, totals, results}."""
    qs = questions or load()
    t0 = time.time()
    results = [judge(q, answer(q["question"], retriever, use_llm=use_llm)) for q in qs]
    totals = {k: sum(r["outcome"] == k for r in results) for k in ("correct", "wrong", "not found")}
    rec = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trigger": trigger, "by": by,
           "method": "language model" if use_llm else "quote documents", "questions": len(results),
           "totals": totals, "seconds": round(time.time() - t0, 1), "results": results}
    with runs_file().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def runs(limit: int = 20) -> list[dict]:
    if not runs_file().exists():
        return []
    lines = [l for l in runs_file().read_text(encoding="utf-8").splitlines() if l.strip()][-limit:]
    return [json.loads(l) for l in reversed(lines)]
