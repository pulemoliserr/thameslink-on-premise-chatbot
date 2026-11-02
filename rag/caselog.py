"""Case log for use case 2: "solutions suggested from similar past tickets".

Staff save resolved cases (situation, decision, Conditions applied). When a new case comes
in, the most similar saved cases are found with the same keyword search used for the
documents. Cases are stored locally in data/caselog.jsonl (on premise).

data/caselog_examples.jsonl holds a few FICTIONAL training examples so the feature can be
demonstrated before real cases exist; they are always labelled "Example" in the app.
Do not store passenger names or other personal data in a case.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from rank_bm25 import BM25Okapi

from . import config
from .retrieve import tokenize

LOG = config.DATA_DIR / "caselog.jsonl"
EXAMPLES = config.DATA_DIR / "caselog_examples.jsonl"


def load_cases(include_examples: bool = True) -> list[dict]:
    cases = []
    for path in ([EXAMPLES] if include_examples else []) + [LOG]:
        if path.exists():
            cases += [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return cases


def add_case(situation: str, decision: str, conditions: str, category: str = "") -> dict:
    case = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "category": category,
            "situation": situation.strip(), "decision": decision.strip(), "conditions": conditions.strip(),
            "example": False}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case


def similar_cases(query: str, k: int = 3, include_examples: bool = True) -> list[tuple[float, dict]]:
    cases = load_cases(include_examples)
    if not cases or not query.strip():
        return []
    docs = [tokenize(f"{c['situation']} {c['decision']} {c['conditions']} {c.get('category', '')}") for c in cases]
    scores = BM25Okapi(docs).get_scores(tokenize(query, expand=True))
    ranked = sorted(zip(scores, cases), key=lambda x: -x[0])
    if not ranked or ranked[0][0] <= 0:
        return []
    cutoff = max(1.0, 0.4 * ranked[0][0])          # drop weak matches that share only a common word
    return [(float(s), c) for s, c in ranked[:k] if s >= cutoff]
