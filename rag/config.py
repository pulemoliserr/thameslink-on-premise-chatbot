"""Settings. Every value can be overridden with an environment variable of the same name."""
import os
from pathlib import Path

APP_VERSION = "2.0.0"

ROOT = Path(__file__).resolve().parent.parent
# Documents shipped with the app (read-only; copied into the library on first start).
BUNDLED_DIR = ROOT / "data"
# The document library: documents.yaml, raw/ (PDFs), curated/ (tables) and index/. Policy owners change it in the
# app, so it lives outside the code (git-ignored "library" folder, or a persistent volume via RAG_DATA_DIR).
DATA_DIR = Path(os.getenv("RAG_DATA_DIR", ROOT / "library"))
INDEX_DIR = Path(os.getenv("RAG_INDEX_DIR", DATA_DIR / "index"))
DOCUMENTS_FILE = DATA_DIR / "documents.yaml"
# Data the app writes (accounts, logs, settings, test set, restore points). Keep it on a persistent volume and back it up.
STATE_DIR = Path(os.getenv("RAG_STATE_DIR", ROOT / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Document uploads and restore points
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "25"))
SNAPSHOTS_DIR = STATE_DIR / "snapshots"
SNAPSHOT_KEEP = int(os.getenv("SNAPSHOT_KEEP", "5"))

# Network guard: "block" (default) refuses any connection to an address outside this machine and the
# local network; "monitor" only records it; "off" disables the guard. See rag/netguard.py.
NETWORK_GUARD = os.getenv("NETWORK_GUARD", "block").strip().lower()
# Extra hosts the app may reach (comma-separated), e.g. a GPU server running Ollama inside the company network.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]

# Public demo (e.g. Streamlit Community Cloud): creates the demo accounts on first start, lists them on the
# sign-in page, and stops visitors from resetting or disabling them. Never set this on a real deployment.
DEMO_MODE = os.getenv("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes", "on")

# Ollama runs the language models locally (https://ollama.com). Nothing leaves the machine.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
# nomic-embed-text expects these task prefixes; set both to "" for other embedding models.
EMBED_QUERY_PREFIX = os.getenv("EMBED_QUERY_PREFIX", "search_query: ")
EMBED_DOC_PREFIX = os.getenv("EMBED_DOC_PREFIX", "search_document: ")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))

# Chunking: target passage size in words.
CHUNK_TARGET_WORDS = int(os.getenv("CHUNK_TARGET_WORDS", "160"))
CHUNK_MAX_WORDS = int(os.getenv("CHUNK_MAX_WORDS", "260"))

# Retrieval
TOP_K = int(os.getenv("TOP_K", "5"))
RRF_K = 60  # constant in reciprocal rank fusion
