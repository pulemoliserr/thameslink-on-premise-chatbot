"""Evaluate the Frontline Assistant on eval/questions.yaml.

    python -m eval.evaluate                 # retrieval + extractive baseline
    python -m eval.evaluate --llm           # also grade language-model answers (needs Ollama)

Metrics
- Retrieval (answerable questions): Hit@1 / Hit@3 / Hit@5 = share of questions where a
  correct passage is in the top 1 / 3 / 5; MRR = mean reciprocal rank of the first
  correct passage (1.0 = always first).
- Answers: correct = every required fact appears in the answer; cited = the answer
  cites at least one correct passage.
- Refusals: on unanswerable questions, the system should say it can't find the answer;
  on answerable ones it should not.
Results are written to eval/results/.
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

import yaml

from rag import config, ollama_client
from rag.answer import NOT_FOUND, answer, coverage
from rag.retrieve import Retriever

HERE = Path(__file__).parent


def load_questions():
    with open(HERE / "questions.yaml", encoding="utf-8") as f:
        qs = yaml.safe_load(f)["questions"]
    for q in qs:
        q.setdefault("answerable", True)
    return qs


def relevant(chunk: dict, q: dict) -> bool:
    return chunk["doc_id"] in q["docs"] and re.search(q["evidence"], chunk["text"]) is not None


def first_relevant_rank(hits, q) -> int | None:
    for r, h in enumerate(hits, start=1):
        if relevant(h.chunk, q):
            return r
    return None


def retrieval_eval(retriever, qs, mode, k=10):
    rows = []
    for q in qs:
        if not q["answerable"]:
            continue
        hits, used = retriever.search(q["question"], k=k, mode=mode)
        rows.append({"id": q["id"], "rank": first_relevant_rank(hits, q), "mode": used})
    n = len(rows)
    def hit(at):
        return sum(1 for r in rows if r["rank"] and r["rank"] <= at) / n
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / n
    return {"mode": rows[0]["mode"], "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr": mrr}, rows


def answer_eval(retriever, qs, use_llm, mode):
    rows = []
    for q in qs:
        a = answer(q["question"], retriever, retrieval_mode=mode, use_llm=use_llm)
        refused = a.text.startswith(NOT_FOUND[:20])
        row = {"id": q["id"], "question": q["question"], "answerable": q["answerable"], "refused": refused,
               "generator": a.generator, "answer": a.text.replace("\n", " ")[:400]}
        if q["answerable"]:
            body = a.text.lower()
            row["correct"] = (not refused) and all(re.search(f, body, re.I) for f in q["facts"])
            cited_chunks = {s.chunk_id for s in a.sources if s.cited}
            row["cited_ok"] = any(relevant(h, q) for h in retriever.chunks if h["id"] in cited_chunks)
        row["coverage"] = round(max(coverage(q["question"], s.text, retriever) for s in a.sources[:3]), 2) if a.sources else 0.0
        rows.append(row)
    ans = [r for r in rows if r["answerable"]]
    un = [r for r in rows if not r["answerable"]]
    return {
        "generator": rows[0]["generator"] if rows else "",
        "correct": sum(r["correct"] for r in ans) / len(ans),
        "cited_ok": sum(r["cited_ok"] for r in ans) / len(ans),
        "false_refusals": sum(r["refused"] for r in ans) / len(ans),
        "correct_refusals": sum(r["refused"] for r in un) / len(un) if un else float("nan"),
    }, rows


def pct(x):
    return f"{x * 100:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="grade language-model answers (needs Ollama)")
    args = ap.parse_args()

    qs = load_questions()
    from rag import library
    library.ensure_index()
    retriever = Retriever.load()
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    n_ans = sum(q["answerable"] for q in qs)
    lines = [f"# Evaluation report — {datetime.now():%Y-%m-%d %H:%M}",
             f"\n{len(qs)} questions ({n_ans} answerable, {len(qs) - n_ans} out of scope), "
             f"{len(retriever.chunks)} passages.\n",
             "## Retrieval\n", "| Method | Hit@1 | Hit@3 | Hit@5 | MRR |", "| --- | --- | --- | --- | --- |"]

    modes = ["bm25"] + (["dense", "hybrid"] if retriever.has_embeddings else [])
    per_q = {q["id"]: {"id": q["id"], "question": q["question"]} for q in qs}
    for m in modes:
        summary, rows = retrieval_eval(retriever, qs, m)
        if summary["mode"] != m:
            print(f"(skipping {m}: embeddings unavailable at query time)")
            continue
        lines.append(f"| {m} | {pct(summary['hit@1'])} | {pct(summary['hit@3'])} | {pct(summary['hit@5'])} "
                     f"| {summary['mrr']:.2f} |")
        for r in rows:
            per_q[r["id"]][f"rank_{m}"] = r["rank"]

    lines += ["\n## Answers\n", "| Generator | Correct | Cites right source | Wrongly refused | Correctly refused (out of scope) |",
              "| --- | --- | --- | --- | --- |"]
    runs = [False]
    if args.llm:
        if ollama_client.has_model(config.CHAT_MODEL):
            runs.append(True)
        else:
            print(f"Ollama / {config.CHAT_MODEL} not available — skipping LLM grading.")
    for use_llm in runs:
        summary, rows = answer_eval(retriever, qs, use_llm, "auto")
        lines.append(f"| {summary['generator']} | {pct(summary['correct'])} | {pct(summary['cited_ok'])} | "
                     f"{pct(summary['false_refusals'])} | {pct(summary['correct_refusals'])} |")
        tag = "llm" if use_llm else "extractive"
        for r in rows:
            per_q[r["id"]].update({f"{tag}_correct": r.get("correct"), f"{tag}_refused": r["refused"],
                                   f"{tag}_answer": r["answer"], "coverage": r["coverage"]})

    lines += ["\n## Per question\n", "Rank = position of the first correct passage (blank = not in top 10).\n",
              "| ID | Question | " + " | ".join(f"Rank {m}" for m in modes) + " | Extractive correct |",
              "| --- | --- | " + " | ".join("---" for _ in modes) + " | --- |"]
    for q in qs:
        r = per_q[q["id"]]
        ranks = " | ".join(str(r.get(f"rank_{m}") or "–") for m in modes) if q["answerable"] else " | ".join("n/a" for _ in modes)
        ok = r.get("extractive_correct") if q["answerable"] else ("refused ✓" if r.get("extractive_refused") else "answered ✗")
        lines.append(f"| {q['id']} | {q['question']} | {ranks} | {ok} |")

    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    with open(out_dir / "per_question.csv", "w", newline="", encoding="utf-8") as f:
        keys = sorted({k for r in per_q.values() for k in r}, key=lambda k: (k != "id", k != "question", k))
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(per_q.values())
    print(report)
    print(f"Saved to {out_dir}/")


if __name__ == "__main__":
    main()
