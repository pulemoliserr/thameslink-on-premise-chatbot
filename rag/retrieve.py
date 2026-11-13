"""Find the passages most relevant to a question.

Two retrievers, combined:
- BM25 keyword search: works everywhere, no model needed; strong on exact terms
  ("Penalty Fare", "Railcard", "Condition 32").
- Semantic search with embeddings from Ollama: finds passages that mean the same
  thing in different words ("wheelchair user" ~ "passenger with reduced mobility").
Results are merged with reciprocal rank fusion (RRF): a passage ranked high by
either method ends up high in the final list.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import numpy as np
import snowballstemmer
from rank_bm25 import BM25Okapi

from . import config, ollama_client

_stem = snowballstemmer.stemmer("english").stemWords
STOPWORDS = set("""a an and are as at be been but by can could do does for from had has have how i if in into is it
its me my no not of on or our should so such than that the their them then there these they this to us was we were
what when where which who whom why will with would you your yours i'm can't don't""".split())
# Rail/policy synonyms that keyword search would otherwise miss.
SYNONYMS = {
    "wheelchair": ["mobility", "accessible"], "disabled": ["disability", "accessible"],
    "refund": ["refunds", "repay"], "compensation": ["delay", "repay"], "cancelled": ["cancellation"],
    "bike": ["cycle"], "bicycle": ["cycle"], "dog": ["animal", "dogs"], "pet": ["animal"],
    "luggage": ["baggage", "articles"], "fine": ["penalty"], "lost": ["property"],
    "help": ["assistance"], "assist": ["assistance"], "ramp": ["ramps", "boarding"],
    "toilet": ["toilets"], "scooter": ["scooters", "mobility"], "taxi": ["alternative", "transport"],
    "bag": ["luggage"], "bags": ["luggage"], "suitcase": ["luggage"], "suitcases": ["luggage"],
    "bikes": ["cycle", "cycles"], "money": ["refund"], "late": ["delay"], "finish": ["complete"],
    "finishing": ["completing"], "staff": ["assistance"], "price": ["fare"], "cost": ["charge", "fare"],
    "broken": ["working", "order"], "closed": ["open"], "bring": ["take"],
    "admin": ["administration"],
}


def synonyms_of(stemmed: str) -> set[str]:
    """Stemmed synonyms of a stemmed word (e.g. 'bicycl' -> {'cycl'})."""
    return _SYN_STEMMED.get(stemmed, set())


def tokenize(text: str, expand: bool = False) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower()) if w not in STOPWORDS]
    if expand:
        words = words + [s for w in words for s in SYNONYMS.get(w, [])]
    return _stem(words)


@dataclass
class Hit:
    chunk: dict
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None


class Retriever:
    def __init__(self, chunks: list[dict], embeddings: np.ndarray | None = None, embed_model: str | None = None):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(search_text(c)) for c in chunks])
        self.embeddings = embeddings
        self.embed_model = embed_model

    @classmethod
    def load(cls) -> "Retriever":
        path = config.INDEX_DIR / "chunks.jsonl"
        if not path.exists():
            raise FileNotFoundError("No search index found. Download the documents and build the index first:  "
                                    "python scripts/download_documents.py  then  python -m rag.ingest")
        chunks = [json.loads(line) for line in open(path, encoding="utf-8")]
        emb, model = None, None
        epath, mpath = config.INDEX_DIR / "embeddings.npy", config.INDEX_DIR / "embeddings.json"
        if epath.exists() and mpath.exists():
            meta = json.loads(mpath.read_text())
            if meta.get("n") == len(chunks):
                emb, model = np.load(epath), meta["model"]
        return cls(chunks, emb, model)

    @property
    def has_embeddings(self) -> bool:
        return self.embeddings is not None

    def bm25_ranking(self, question: str) -> np.ndarray:
        return np.asarray(self.bm25.get_scores(tokenize(question, expand=True)))

    def dense_ranking(self, question: str) -> np.ndarray | None:
        if self.embeddings is None:
            return None
        try:
            q = np.asarray(ollama_client.embed([config.EMBED_QUERY_PREFIX + question], self.embed_model)[0])
        except ollama_client.OllamaError:
            return None
        q = q / (np.linalg.norm(q) or 1.0)
        return self.embeddings @ q

    def search(self, question: str, k: int = config.TOP_K, mode: str = "auto",
               scope=None) -> tuple[list[Hit], str]:
        """mode: 'bm25', 'dense', 'hybrid' or 'auto' (hybrid when embeddings work, else bm25).
        scope: optional function chunk -> bool; passages outside the scope are never returned
        (used to give each use case its own document set).
        Returns the hits and the mode actually used."""
        bm25 = self.bm25_ranking(question)
        dense = self.dense_ranking(question) if mode in ("auto", "hybrid", "dense") else None
        if scope is not None:
            mask = np.array([bool(scope(c)) for c in self.chunks])
            bm25 = np.where(mask, bm25, 0.0)
            if dense is not None:
                dense = np.where(mask, dense, -np.inf)
        if dense is None:
            mode_used = "bm25"
        else:
            mode_used = "dense" if mode == "dense" else "hybrid"

        bm25_order = [i for i in np.argsort(-bm25) if bm25[i] > 0]
        bm25_rank = {i: r for r, i in enumerate(bm25_order)}
        if mode_used == "bm25":
            return [Hit(self.chunks[i], float(bm25[i]), bm25_rank[i]) for i in bm25_order[:k]], mode_used

        dense_order = [i for i in np.argsort(-dense) if np.isfinite(dense[i])]
        dense_rank = {i: r for r, i in enumerate(dense_order)}
        if mode_used == "dense":
            return [Hit(self.chunks[i], float(dense[i]), bm25_rank.get(i), dense_rank[i])
                    for i in dense_order[:k]], mode_used

        fused: dict[int, float] = {}
        for ranks in (bm25_rank, dense_rank):
            for i, r in ranks.items():
                fused[i] = fused.get(i, 0.0) + 1.0 / (config.RRF_K + r + 1)
        order = sorted(fused, key=fused.get, reverse=True)[:k]
        return [Hit(self.chunks[i], fused[i], bm25_rank.get(i), dense_rank.get(i)) for i in order], mode_used


def search_text(c: dict) -> str:
    return " > ".join(c["section_path"]) + "\n" + c.get("label", "") + "\n" + c["text"]


def build_embeddings(chunks) -> None:
    """Embed every passage with Ollama and store the vectors next to the index."""
    from dataclasses import asdict, is_dataclass
    chunks = [asdict(c) if is_dataclass(c) else c for c in chunks]
    if not ollama_client.has_model(config.EMBED_MODEL):
        raise SystemExit(f"Ollama is not running or '{config.EMBED_MODEL}' is not installed.\n"
                         f"Start Ollama and run:  ollama pull {config.EMBED_MODEL}")
    vectors = []
    batch = 16
    for start in range(0, len(chunks), batch):
        texts = [config.EMBED_DOC_PREFIX + search_text(c) for c in chunks[start:start + batch]]
        vectors.extend(ollama_client.embed(texts))
        print(f"  embedded {min(start + batch, len(chunks))}/{len(chunks)}", end="\r")
    m = np.asarray(vectors, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True).clip(min=1e-9)
    np.save(config.INDEX_DIR / "embeddings.npy", m)
    (config.INDEX_DIR / "embeddings.json").write_text(json.dumps({"model": config.EMBED_MODEL, "n": len(chunks)}))
    print(f"\nSaved {len(chunks)} embeddings ({m.shape[1]} dimensions, model {config.EMBED_MODEL}).")


_SYN_STEMMED = {}
for _w, _syns in SYNONYMS.items():
    _SYN_STEMMED.setdefault(_stem([_w])[0], set()).update(_stem(_syns))
