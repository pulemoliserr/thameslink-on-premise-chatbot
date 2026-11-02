"""Settings. Every value can be overridden with an environment variable of the same name."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RAG_DATA_DIR", ROOT / "data"))
INDEX_DIR = Path(os.getenv("RAG_INDEX_DIR", DATA_DIR / "index"))
DOCUMENTS_FILE = DATA_DIR / "documents.yaml"

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
