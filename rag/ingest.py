"""Turn the documents in data/documents.yaml into searchable passages ("chunks").

Run:  python -m rag.ingest            (keyword index only)
      python -m rag.ingest --embed    (also compute embeddings with Ollama)

Steps
1. Read each PDF line by line, keeping font size and boldness.
2. Detect headings: lines clearly larger than body text, or bold numbered
   lines such as "30. Your Right to a Refund ...".
3. Group lines into paragraphs, then pack paragraphs into passages of about
   CHUNK_TARGET_WORDS words that never cross a heading.
4. Store each passage with its document, section and page numbers, so every
   answer can cite exactly where it came from.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import config

PAGE_FOOTER = re.compile(r"^(page\s+)?\d{1,3}$", re.I)
NUMBERED_HEADING = re.compile(r"^(\d{1,2}\.|[A-H]\.\d+(\.\d+)*|[A-H]\.|Part [A-H]:|Appendix [A-C]:?)(\s+\S|$)")
LARGE = 1.4          # a line this many times the body font size is a heading
MIN_CHUNK_WORDS = 30 # smaller passages are merged into a neighbour
CLAUSE_START = re.compile(r"^(\d{1,2}\.\d{1,2})\s")
BULLET_START = re.compile(r"^[•\-–▪●]\s?")


@dataclass
class Line:
    text: str
    size: float
    bold: bool
    top: float
    page: int
    x0: float = 0.0


@dataclass
class Chunk:
    id: str
    doc_id: str
    doc_title: str
    doc_short: str
    section: str
    section_path: list[str]
    page_start: int
    page_end: int
    text: str
    label: str = ""
    clause: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        """Text used for retrieval: the heading path gives each passage its context."""
        return " > ".join(self.section_path) + "\n" + self.text


# ---------------------------------------------------------------- PDF reading

def read_lines(pdf_path: Path, skip_pages: set[int]) -> list[Line]:
    import pdfplumber

    lines: list[Line] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            if page_no in skip_pages:
                continue
            for raw in page.extract_text_lines(return_chars=True):
                text = " ".join(raw["text"].split())
                if (not text or PAGE_FOOTER.match(text) or looks_garbled(text)
                        or raw["top"] < 0 or raw["x0"] < 0):   # off-page / rotated decoration
                    continue
                chars = [c for c in raw["chars"] if c["text"].strip()]
                if not chars:
                    continue
                size = round(max(c["size"] for c in chars), 1)
                bold_share = sum("bold" in c["fontname"].lower() for c in chars) / len(chars)
                lines.append(Line(text, size, bold_share > 0.6, raw["top"], page_no, raw["x0"]))
    if lines:
        body = body_size(lines)
        lines = [l for l in lines if l.size >= body * 0.6]   # drop tiny print (logos, labels)
    return lines


def looks_garbled(text: str) -> bool:
    """Rotated or decorative text comes out as single letters: 'n a or y f pl p A'."""
    tokens = text.split()
    return len(tokens) >= 5 and sum(len(t) <= 2 for t in tokens) / len(tokens) > 0.6


def body_size(lines: list[Line]) -> float:
    return statistics.mode(l.size for l in lines)


def heading_level(line: Line, body: float) -> float | None:
    """Return a heading 'weight' (bigger = more important) or None for body text."""
    if line.size >= body * LARGE and len(line.text) < 90:
        return line.size
    if line.bold and NUMBERED_HEADING.match(line.text) and len(line.text) < 110 \
            and not CLAUSE_START.match(line.text):
        return body + (0.5 if line.text.startswith(("Part ", "Appendix ")) else 0.1)
    return None


# ---------------------------------------------------------------- structuring

def to_paragraphs(lines: list[Line], body: float):
    """Yield (heading_path, paragraph_text, page_start, page_end, clause)."""
    path: list[tuple[float, str]] = []
    para: list[Line] = []
    prev: Line | None = None
    pending_heading: list[Line] = []

    def flush():
        nonlocal para
        if para:
            text = join_lines([l.text for l in para])
            clause = ""
            m = CLAUSE_START.match(para[0].text)
            if m:
                clause = m.group(1)
            yield [h for _, h in path], text, para[0].page, para[-1].page, clause
        para = []

    def commit_heading():
        nonlocal pending_heading
        if pending_heading:
            # chapter-size headings (>= 2x body) are siblings, whatever their exact size
            weight = (100.0 if max(l.size for l in pending_heading) >= body * 2
                      else max(l.size for l in pending_heading)) if pending_heading[0].size >= body * LARGE \
                else heading_level(pending_heading[0], body)
            text = join_lines([l.text for l in reading_order(pending_heading)])
            while path and path[-1][0] <= weight:
                path.pop()
            path.append((weight, text))
        pending_heading = []

    for line in lines:
        level = heading_level(line, body)
        if level is not None:
            # consecutive large lines form one heading ("Assistance / what is available / ...")
            if pending_heading and not (pending_heading[-1].page == line.page
                                        and pending_heading[-1].size >= body * LARGE
                                        and line.size >= body * LARGE
                                        # a much smaller line is a sub-heading, not a continuation
                                        and line.size >= 0.7 * min(l.size for l in pending_heading)):
                commit_heading()
            if not pending_heading:
                yield from flush()
            pending_heading.append(line)
            prev = line
            continue
        if (pending_heading and line.bold and line.page == pending_heading[-1].page
                and pending_heading[0].size < body * LARGE          # only small numbered headings wrap
                and line.top - pending_heading[-1].top < line.size * 2.0):
            pending_heading.append(line)   # heading wrapped onto a second line
            prev = line
            continue
        commit_heading()

        new_para = (
            prev is None
            or CLAUSE_START.match(line.text)
            or BULLET_START.match(line.text)
            or (line.bold and len(line.text.split()) <= 10 and not (prev and prev.bold))
            or (prev.page == line.page and line.top - prev.top > line.size * 2.0)
        )
        if new_para:
            yield from flush()
        para.append(line)
        prev = line
    commit_heading()
    yield from flush()


def reading_order(lines: list[Line]) -> list[Line]:
    """Big display headings mix sizes on one visual row ('go as' + 'planned'): sort by row, then x."""
    rows: list[list[Line]] = []
    for l in sorted(lines, key=lambda l: l.top):
        if rows and abs(l.top - rows[-1][0].top) < 0.6 * min(l.size, rows[-1][0].size):
            rows[-1].append(l)
        else:
            rows.append([l])
    return [l for row in rows for l in sorted(row, key=lambda l: l.x0)]


def join_lines(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out.endswith("-") and p[:1].islower():
            out += p          # hyphenated line break: "support-" + "travelling"
        elif out:
            out += " " + p
        else:
            out = p
    return out


def _words(t: str) -> int:
    return len(t.split())


def pack_chunks(paragraphs, doc: dict) -> list[Chunk]:
    groups: list[list[tuple]] = []   # each group = paragraphs of one passage
    buf: list[tuple] = []

    def size(b):
        return sum(_words(p[1]) for p in b)

    def emit():
        nonlocal buf
        if buf:
            groups.append(buf)
        buf = []

    for para in paragraphs:
        path, text = para[0], para[1]
        if buf and (path != buf[0][0]
                    or size(buf) + _words(text) > config.CHUNK_MAX_WORDS
                    or size(buf) >= config.CHUNK_TARGET_WORDS):
            emit()
        if _words(text) > config.CHUNK_MAX_WORDS:      # very long paragraph: split by sentence
            for piece in split_long(text, config.CHUNK_TARGET_WORDS):
                buf.append((path, piece, para[2], para[3], para[4]))
                emit()
            continue
        buf.append(para)
    emit()

    # Merge fragments (a lone link list, a one-line clause) into a neighbour in the same section;
    # a fragment that only shares the document goes into the following passage.
    merged: list[list[tuple]] = []
    carry: list[tuple] = []
    for g in groups:
        g = carry + g
        carry = []
        if size(g) < MIN_CHUNK_WORDS:
            if merged and merged[-1][0][0] == g[0][0] and size(merged[-1]) + size(g) <= config.CHUNK_MAX_WORDS + 40:
                merged[-1].extend(g)
            else:
                carry = g
            continue
        merged.append(g)
    if carry:
        if merged:
            merged[-1].extend(carry)
        else:
            merged.append(carry)

    chunks = []
    for g in merged:
        text = "\n".join(p[1] for p in g)
        clauses = [p[4] for p in g if p[4]]
        chunks.append(make_chunk(doc, len(chunks), g[-1][0], text, g[0][2], g[-1][3], clauses))
    return chunks


def split_long(text: str, target: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], []
    for s in sentences:
        cur.append(s)
        if len(" ".join(cur).split()) >= target:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def make_chunk(doc, n, path, text, p0, p1, clauses) -> Chunk:
    section = path[-1] if path else doc["title"]
    label = section
    clause = clauses[0] if clauses else ""
    in_appendix = any(p.startswith("Appendix") for p in path)
    if in_appendix and len(path) > 1:
        label = f"{path[0].split(':')[0]}, {section}"
    elif doc.get("section_style") == "condition":
        m = re.match(r"^(\d{1,2})\.\s+(.*)", section)
        if m:
            nums = sorted({c for c in clauses if c.split(".")[0] == m.group(1)},
                          key=lambda c: int(c.split(".")[1]))
            if len(nums) > 1:
                label = f"Conditions {nums[0]}–{nums[-1]} ({m.group(2)})"
            elif nums:
                label = f"Condition {nums[0]} ({m.group(2)})"
            else:
                label = f"Condition {m.group(1)} ({m.group(2)})"
    return Chunk(
        id=f"{doc['id']}-{n:03d}", doc_id=doc["id"], doc_title=doc["title"],
        doc_short=doc["short"], section=section, section_path=list(path),
        page_start=p0, page_end=p1, text=text, label=label, clause=clause,
    )


# ---------------------------------------------------------------- tables

FLEET_FIELDS = {
    "dedicated_accessible_carriage_with_wheelchair_space": "dedicated accessible carriage with space for a wheelchair and user",
    "standard_toilet": "standard toilet",
    "accessible_toilet": "accessible toilet (transfer area, colour-contrasting features, support rails, call-for-aid)",
    "aural_information": "aural (audio) information",
    "visual_information": "visual information",
    "priority_seats": "priority seats",
    "accessible_signage_outside_train": "accessible signage on outside of train",
    "scooter_mobility_aid_acceptance": "scooter / mobility aid acceptance",
    "boarding_ramp": "boarding ramp",
    "on_train_staff": "on-train staff",
}


def table_chunks(doc: dict) -> list[Chunk]:
    path = config.DATA_DIR / doc["file"]
    chunks = []
    with open(path, newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f)):
            facts = "; ".join(f"{label}: {row[key]}" for key, label in FLEET_FIELDS.items())
            text = (f"{row['brand']} Class {row['train_class']} trains (routes: {row['routes']}). "
                    f"Accessibility features — {facts}.")
            section = f"{row['brand']} Class {row['train_class']}"
            c = make_chunk(doc, n, [section], text, int(row["page"]), int(row["page"]), [])
            c.extra = {"row": row}
            chunks.append(c)
    return chunks


# ---------------------------------------------------------------- main

def load_documents() -> list[dict]:
    with open(config.DATA_DIR / "documents.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["documents"]


def build_chunks() -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for doc in load_documents():
        if doc["kind"] == "table":
            chunks = table_chunks(doc)
        else:
            lines = read_lines(config.DATA_DIR / doc["file"], set(doc.get("skip_pages", [])))
            chunks = pack_chunks(to_paragraphs(lines, body_size(lines)), doc)
        print(f"  {doc['short']:<26} {len(chunks):>4} passages")
        all_chunks.extend(chunks)
    return all_chunks


def save_chunks(chunks: list[Chunk]) -> Path:
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    out = config.INDEX_DIR / "chunks.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--embed", action="store_true", help="also compute embeddings with Ollama (if it is running)")
    args = ap.parse_args()
    from . import library, ollama_client
    library.ensure_library()
    print(f"Reading documents in {config.DATA_DIR} …")
    n = library.build_index()            # also rebuilds embeddings when Ollama has the embedding model
    print(f"Saved {n} passages to {library.index_file()}")
    if args.embed and not ollama_client.has_model(config.EMBED_MODEL):
        print(f"Embeddings skipped: start Ollama and run  ollama pull {config.EMBED_MODEL}")


if __name__ == "__main__":
    main()
