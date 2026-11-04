"""Smoke tests.  Run:  python -m pytest -q

They use the real PDFs in data/raw and a fake Ollama server (tests/mock_ollama.py),
so they run without any language model installed.
"""
import importlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def chunks():
    from rag import ingest, library
    library.ensure_library()          # first run: copy the bundled documents into the library folder
    return ingest.build_chunks()


@pytest.fixture(scope="session")
def retriever(chunks):
    from dataclasses import asdict
    from rag.retrieve import Retriever
    return Retriever([asdict(c) for c in chunks])


def test_every_document_is_indexed(chunks):
    assert {c.doc_id for c in chunks} == {"nrcot", "atp", "mra", "fleet"}
    assert all(c.text.strip() for c in chunks)


def test_conditions_are_labelled(chunks):
    labels = [c.label for c in chunks if c.doc_id == "nrcot"]
    assert any(l.startswith("Condition 24") or l.startswith("Conditions 24") for l in labels)
    assert not any(l.startswith("Condition 1 (Requirements") for l in labels), "appendix items are not Conditions"


def test_passages_have_sane_size(chunks):
    sizes = [len(c.text.split()) for c in chunks if c.doc_id != "fleet"]
    assert min(sizes) >= 20 and max(sizes) <= 320


def test_keyword_search_finds_fleet_row(retriever):
    hits, mode = retriever.search("Is there an accessible toilet on a Class 717 train?", mode="bm25")
    assert mode == "bm25"
    assert hits[0].chunk["id"].startswith("fleet") and "717" in hits[0].chunk["text"]


def test_extractive_answer_cites_sources(retriever):
    from rag.answer import answer
    a = answer("How many dogs can a passenger take?", retriever, use_llm=False)
    assert a.found and any(s.cited for s in a.sources)
    assert "two dogs" in a.text


def test_out_of_scope_question_is_refused(retriever):
    from rag.answer import NOT_FOUND, answer
    a = answer("What is the Wi-Fi password on Class 700 trains?", retriever, use_llm=False)
    assert a.text == NOT_FOUND and not a.found


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def mock_ollama(monkeypatch):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_ollama.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{port}")
    from rag import config
    importlib.reload(config)
    yield
    proc.terminate()
    monkeypatch.delenv("OLLAMA_URL")
    importlib.reload(config)


def test_llm_and_hybrid_path(mock_ollama, chunks):
    """Full path: embeddings -> hybrid search -> prompt -> cited answer (with a fake model)."""
    from dataclasses import asdict
    import numpy as np
    from rag import ollama_client
    from rag.answer import answer
    from rag.retrieve import Retriever, search_text

    docs = [asdict(c) for c in chunks]
    vecs = np.asarray(ollama_client.embed([search_text(c) for c in docs]), dtype=np.float32)
    r = Retriever(docs, vecs, "nomic-embed-text")
    a = answer("Can a passenger take their dog on the train?", r, use_llm=True)
    assert a.retrieval_mode == "hybrid"
    assert a.generator.startswith("llm:")
    assert any(s.cited for s in a.sources)
