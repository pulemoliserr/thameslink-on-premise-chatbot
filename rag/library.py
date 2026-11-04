"""The document library: what the chatbot knows, and how policy owners change it.

User stories covered (policy owner and IT & data protection):
- Upload and replace: a new or revised PDF is checked, indexed and used straight away, without
  developer help. The old version is no longer used. If anything fails, a clear error is shown and
  the previous version stays in use (the change runs against a restore point).
- Overview: title, version, pages, passages and last update for every document.
- Remove: needs confirmation; the PDF, its passages, its embeddings, copies in restore points and
  answers in saved chat histories are all removed (`leftovers` checks that nothing stays behind).
- Control: only named document editors may change documents (checked here, on the server);
  every change is written to the audit log.
- Maintenance: a restore point is taken before every change; IT can restore one after a failed update.

Paths are read from `config` at call time, so the library can live on a persistent volume
(RAG_DATA_DIR) and tests can point it at a temporary folder.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import auth, config, ingest

HEADER = """# The document library: the only knowledge the chatbot may use.
# Managed in the app by policy owners who are document editors (Documents area).
# kind: pdf   -> text is extracted from the PDF and split into passages by heading
# kind: table -> a hand-curated CSV (one passage per row), used where PDF text
#                extraction scrambles a table layout
"""
SECTION_STYLES = {"heading": "Headings (e.g. “A.5 Wheelchairs…”)", "condition": "Numbered conditions (cited as “Condition 30.2”)"}


class LibraryError(Exception):
    """A problem the policy owner can act on; the message is safe to show on screen."""


# ---------------------------------------------------------------- paths and registry
def docs_file() -> Path:
    return config.DATA_DIR / "documents.yaml"


def index_file() -> Path:
    return config.INDEX_DIR / "chunks.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def registry() -> list[dict]:
    if not docs_file().exists():
        return []
    return yaml.safe_load(docs_file().read_text(encoding="utf-8")).get("documents") or []


def save_registry(docs: list[dict]) -> None:
    docs_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = docs_file().with_suffix(".tmp")
    tmp.write_text(HEADER + "\n" + yaml.safe_dump({"documents": docs}, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(docs_file())


def get(doc_id: str) -> dict | None:
    return next((d for d in registry() if d["id"] == doc_id), None)


def doc_path(doc: dict) -> Path:
    return config.DATA_DIR / doc["file"]


def source_pdf(doc: dict) -> Path | None:
    """The PDF behind a document (for a table, the PDF it was transcribed from), if present."""
    rel = doc.get("source_pdf") or (doc["file"] if doc.get("kind", "pdf") == "pdf" else None)
    path = config.DATA_DIR / rel if rel else None
    return path if path and path.exists() else None


def ensure_library() -> None:
    """First start: copy the bundled documents (data/) into the library folder. Later starts: copy any bundled
    file that is still missing (e.g. PDFs downloaded after the first start). Records page counts and dates."""
    same = config.DATA_DIR.resolve() == config.BUNDLED_DIR.resolve()
    if not same and not docs_file().exists() and (config.BUNDLED_DIR / "documents.yaml").exists():
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.BUNDLED_DIR / "documents.yaml", docs_file())
    docs = registry()
    changed = False
    for d in docs:
        if d.get("bundled", "updated_by" not in d) and not same:
            for rel in {d["file"], d.get("source_pdf")} - {None}:
                src, dst = config.BUNDLED_DIR / rel, config.DATA_DIR / rel
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        if "pages" not in d and source_pdf(d):
            d["pages"] = _page_count(source_pdf(d).read_bytes())
            changed = True
        if "updated" not in d and doc_path(d).exists():
            d["updated"] = datetime.fromtimestamp(doc_path(d).stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            d["updated_by"] = "initial document set"
            d["bundled"] = True
            changed = True
    if changed:
        save_registry(docs)


def _page_count(data: bytes) -> int:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return len(pdf.pages)


# ---------------------------------------------------------------- index
def index_version() -> int:
    """Changes whenever the index changes (used to reload the search index in the app)."""
    return index_file().stat().st_mtime_ns if index_file().exists() else 0


def load_chunks() -> list[dict]:
    if not index_file().exists():
        return []
    return [json.loads(line) for line in index_file().open(encoding="utf-8") if line.strip()]


def chunks_for(doc: dict, path: Path | None = None) -> list[ingest.Chunk]:
    if doc.get("kind", "pdf") == "table":
        return ingest.table_chunks(doc)
    lines = ingest.read_lines(path or doc_path(doc), set(doc.get("skip_pages", [])))
    if not lines:
        return []
    return ingest.pack_chunks(ingest.to_paragraphs(lines, ingest.body_size(lines)), doc)


def _write_index(chunks: list[dict]) -> None:
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = index_file().with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    tmp.replace(index_file())
    _refresh_embeddings(chunks)


def _refresh_embeddings(chunks: list[dict]) -> None:
    """Embeddings must match the passages exactly: rebuild them when Ollama is available, else delete them
    (search then falls back to keywords until IT rebuilds them)."""
    for name in ("embeddings.npy", "embeddings.json"):
        (config.INDEX_DIR / name).unlink(missing_ok=True)
    from . import ollama_client
    if chunks and ollama_client.has_model(config.EMBED_MODEL):
        from .retrieve import build_embeddings
        try:
            build_embeddings(chunks)
        except (SystemExit, ollama_client.OllamaError):
            for name in ("embeddings.npy", "embeddings.json"):
                (config.INDEX_DIR / name).unlink(missing_ok=True)


def ensure_index() -> None:
    """Seed the library and build the index if it does not exist yet (tests, evaluation, command line)."""
    ensure_library()
    if not index_file().exists():
        build_index()


def build_index() -> int:
    """Rebuild the whole index from the registry. Returns the number of passages."""
    chunks = [asdict(c) for d in registry() if doc_path(d).exists() for c in chunks_for(d)]
    _write_index(chunks)
    return len(chunks)


# ---------------------------------------------------------------- overview
def overview() -> list[dict]:
    counts: dict[str, int] = {}
    for c in load_chunks():
        counts[c["doc_id"]] = counts.get(c["doc_id"], 0) + 1
    rows = []
    for d in registry():
        path = doc_path(d)
        rows.append({
            "id": d["id"], "title": d["title"], "short": d["short"], "version": d.get("version", ""),
            "publisher": d.get("credit", ""), "kind": "Table (CSV)" if d.get("kind") == "table" else "PDF",
            "pages": d.get("pages", ""), "passages": counts.get(d["id"], 0),
            "updated": (d.get("updated") or "")[:10], "updated_by": d.get("updated_by", ""),
            "size_kb": round(path.stat().st_size / 1024) if path.exists() else 0,
            "protected": is_protected(d),
        })
    return rows


def is_protected(doc: dict) -> bool:
    """In the public demo, the bundled documents cannot be removed or replaced (so the demo keeps working)."""
    return config.DEMO_MODE and bool(doc.get("bundled"))


# ---------------------------------------------------------------- checking an upload
def check_pdf(data: bytes) -> dict:
    """Check an uploaded file. Returns {pages, words}; raises LibraryError with a clear message."""
    if not data:
        raise LibraryError("The file is empty.")
    mb = len(data) / 1_048_576
    if mb > config.MAX_UPLOAD_MB:
        raise LibraryError(f"The file is {mb:.1f} MB; the limit is {config.MAX_UPLOAD_MB:.0f} MB.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise LibraryError("This is not a PDF file. Upload the document as a PDF.")
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            words = sum(len((p.extract_text() or "").split()) for p in pdf.pages)
    except Exception as e:                                     # damaged or password-protected
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            raise LibraryError("The PDF is password-protected. Upload a version without a password.") from e
        raise LibraryError("The PDF could not be read. It may be damaged; try exporting it again.") from e
    if pages == 0:
        raise LibraryError("The PDF has no pages.")
    if words < 50:
        raise LibraryError("No text could be read from this PDF. It is probably a scan; upload a PDF with "
                           "selectable text (or run text recognition on it first).")
    return {"pages": pages, "words": words}


def make_id(short: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", short.lower()).strip("-")[:30] or "document"
    doc_id, n = base, 2
    while doc_id in taken:
        doc_id, n = f"{base}-{n}", n + 1
    return doc_id


# ---------------------------------------------------------------- changes
def add_document(actor, data: bytes, title: str, short: str, version: str, credit: str = "",
                 section_style: str = "heading", skip_pages: list[int] | None = None,
                 replace_id: str | None = None) -> dict:
    """Upload a new document, or a new version of `replace_id`. Returns {doc, passages, pages}."""
    auth.require(actor, "edit_documents")
    by = actor if isinstance(actor, str) else actor.username
    title, short, version = title.strip(), short.strip(), version.strip()
    if not title or not short:
        raise LibraryError("Give the document a title and a short name.")
    if not version:
        raise LibraryError("Give the version or date of the document, so staff can see which version answers come from.")
    if section_style not in SECTION_STYLES:
        raise LibraryError("Unknown section style.")
    docs = registry()
    old = next((d for d in docs if d["id"] == replace_id), None) if replace_id else None
    if replace_id and old is None:
        raise LibraryError("The document to replace no longer exists.")
    if old and old.get("kind") == "table":
        raise LibraryError("This document is a curated table; it cannot be replaced by a PDF.")
    if old and is_protected(old):
        raise LibraryError("This document is protected in the public demo. Upload it as a new document instead.")
    if any(d["short"].lower() == short.lower() and d["id"] != replace_id for d in docs):
        raise LibraryError(f"Another document is already called “{short}”. Choose a different short name.")
    info = check_pdf(data)

    doc_id = old["id"] if old else make_id(short, {d["id"] for d in docs})
    doc = {"id": doc_id, "kind": "pdf", "file": f"raw/{doc_id}.pdf", "title": title, "short": short,
           "version": version, "credit": credit.strip(), "section_style": section_style,
           "skip_pages": sorted(set(skip_pages or [])), "pages": info["pages"],
           "sha256": hashlib.sha256(data).hexdigest(), "updated": _now(), "updated_by": by}

    # Build the passages from a temporary copy first: nothing changes if this fails.
    tmp_dir = config.STATE_DIR / "upload_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{doc_id}-{time.time_ns()}.pdf"
    tmp.write_bytes(data)
    try:
        new_chunks = [asdict(c) for c in chunks_for(doc, tmp)]
    except Exception as e:
        raise LibraryError(f"The PDF could not be split into passages ({e}).") from e
    finally:
        tmp.unlink(missing_ok=True)
    if not new_chunks:
        raise LibraryError("No usable text passages were found in this PDF.")

    snap = snapshot(f"before {'replacing' if old else 'adding'} {short}", by)
    try:
        if old and doc_path(old).exists() and old["file"] != doc["file"]:
            doc_path(old).unlink()
        doc_path(doc).parent.mkdir(parents=True, exist_ok=True)
        doc_path(doc).write_bytes(data)
        docs = [doc if d["id"] == doc_id else d for d in docs] if old else docs + [doc]
        save_registry(docs)
        _write_index([c for c in load_chunks() if c["doc_id"] != doc_id] + new_chunks)
    except Exception as e:
        _restore_files(snap)
        raise LibraryError(f"The update failed and was undone; the previous version is still in use ({e}).") from e
    auth.audit("document_replaced" if old else "document_added", doc_id, by=by,
               detail=f"{short} · {version} · {info['pages']} pages · {len(new_chunks)} passages")
    return {"doc": doc, "passages": len(new_chunks), "pages": info["pages"], "replaced": bool(old)}


def remove_document(actor, doc_id: str, confirm: str) -> list[str]:
    """Remove a document and every copy of it. `confirm` must be the document's short name.
    Returns the leftover check (empty = nothing stayed behind)."""
    auth.require(actor, "edit_documents")
    by = actor if isinstance(actor, str) else actor.username
    doc = get(doc_id)
    if doc is None:
        raise LibraryError("This document no longer exists.")
    if is_protected(doc):
        raise LibraryError("This document is protected in the public demo.")
    if confirm.strip().lower() != doc["short"].lower():
        raise LibraryError(f"To confirm, type the short name exactly: {doc['short']}")
    snapshot(f"before removing {doc['short']}", by)      # restore point for the OTHER documents
    for rel in {doc["file"], doc.get("source_pdf")} - {None}:
        (config.DATA_DIR / rel).unlink(missing_ok=True)
    save_registry([d for d in registry() if d["id"] != doc_id])
    _write_index([c for c in load_chunks() if c["doc_id"] != doc_id])
    purge_from_snapshots(doc)
    from . import history
    history.forget_document(doc_id)
    auth.audit("document_removed", doc_id, by=by, detail=f"{doc['short']} · {doc.get('version', '')}")
    return leftovers(doc)


def leftovers(doc: dict) -> list[str]:
    """Places where a removed document can still be found. Empty list = removed everywhere."""
    found = []
    doc_id = doc["id"]
    if any(d["id"] == doc_id for d in registry()):
        found.append("still listed in the document library")
    for rel in {doc.get("file"), doc.get("source_pdf")} - {None}:
        if (config.DATA_DIR / rel).exists():
            found.append(f"file {rel}")
    if any(c["doc_id"] == doc_id for c in load_chunks()):
        found.append("passages in the search index")
    meta = config.INDEX_DIR / "embeddings.json"
    if meta.exists() and json.loads(meta.read_text()).get("n") != len(load_chunks()):
        found.append("embeddings out of date")
    for snap in snapshots():
        if doc_id in snap.get("documents", []):
            found.append(f"restore point {snap['id']}")
    from . import history
    if history.mentions_document(doc_id):
        found.append("saved chat histories")
    return found


def pdf_bytes(doc_id: str) -> bytes | None:
    doc = get(doc_id)
    path = source_pdf(doc) if doc else None
    return path.read_bytes() if path else None


# ---------------------------------------------------------------- restore points
def _snap_dir(snap_id: str) -> Path:
    if not re.fullmatch(r"[0-9TZ\-]+", snap_id):
        raise LibraryError("Unknown restore point.")
    return config.SNAPSHOTS_DIR / snap_id


def snapshots() -> list[dict]:
    if not config.SNAPSHOTS_DIR.exists():
        return []
    metas = []
    for d in config.SNAPSHOTS_DIR.iterdir():
        meta = d / "meta.json"
        if meta.exists():
            metas.append(json.loads(meta.read_text(encoding="utf-8")))
    return sorted(metas, key=lambda m: m["id"], reverse=True)


def snapshot(reason: str, by: str) -> str:
    """Copy the document library (documents, passages, embeddings) into a restore point."""
    snap_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    target = config.SNAPSHOTS_DIR / snap_id
    target.mkdir(parents=True)
    if docs_file().exists():
        shutil.copy2(docs_file(), target / "documents.yaml")
    for part in ("raw", "curated"):
        if (config.DATA_DIR / part).is_dir():
            shutil.copytree(config.DATA_DIR / part, target / part)
    if config.INDEX_DIR.is_dir():
        shutil.copytree(config.INDEX_DIR, target / "index", ignore=shutil.ignore_patterns("*.tmp"))
    meta = {"id": snap_id, "time": _now(), "reason": reason, "by": by, "version": config.APP_VERSION,
            "documents": [d["id"] for d in registry()]}
    (target / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    for old in snapshots()[config.SNAPSHOT_KEEP:]:
        shutil.rmtree(config.SNAPSHOTS_DIR / old["id"], ignore_errors=True)
    return snap_id


def _restore_files(snap_id: str) -> None:
    src = _snap_dir(snap_id)
    for part in ("raw", "curated", "index"):
        dst = (config.INDEX_DIR if part == "index" else config.DATA_DIR / part)
        if dst.exists():
            shutil.rmtree(dst)
        if (src / part).exists():
            shutil.copytree(src / part, dst)
    if (src / "documents.yaml").exists():
        shutil.copy2(src / "documents.yaml", docs_file())


def restore(actor, snap_id: str) -> None:
    """Go back to a restore point (IT). The current state is saved as a restore point first."""
    auth.require(actor, "manage_backups")
    by = actor if isinstance(actor, str) else actor.username
    if not (_snap_dir(snap_id) / "meta.json").exists():
        raise LibraryError("Unknown restore point.")
    snapshot("before restoring an earlier version", by)
    _restore_files(snap_id)
    index_file().touch()                     # new index version: the app reloads the search index
    auth.audit("library_restored", snap_id, by=by)


def purge_from_snapshots(doc: dict) -> None:
    """Deletion story: remove a removed document from every restore point too."""
    for meta in snapshots():
        snap = config.SNAPSHOTS_DIR / meta["id"]
        if doc["id"] not in meta.get("documents", []):
            continue
        for rel in {doc.get("file"), doc.get("source_pdf")} - {None}:
            (snap / rel).unlink(missing_ok=True)
        y = snap / "documents.yaml"
        if y.exists():
            docs = [d for d in (yaml.safe_load(y.read_text(encoding="utf-8")).get("documents") or [])
                    if d["id"] != doc["id"]]
            y.write_text(HEADER + "\n" + yaml.safe_dump({"documents": docs}, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
        idx = snap / "index" / "chunks.jsonl"
        if idx.exists():
            keep = [l for l in idx.read_text(encoding="utf-8").splitlines()
                    if l.strip() and json.loads(l)["doc_id"] != doc["id"]]
            idx.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        for name in ("embeddings.npy", "embeddings.json"):
            (snap / "index" / name).unlink(missing_ok=True)
        meta["documents"] = [d for d in meta["documents"] if d != doc["id"]]
        meta["purged"] = meta.get("purged", []) + [doc["id"]]
        (snap / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
