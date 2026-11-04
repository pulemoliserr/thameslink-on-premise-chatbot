"""Skip the document-based tests when the source PDFs have not been downloaded yet."""
from pathlib import Path

import pytest

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
NEEDED = ["National_Rail_Conditions_of_Travel_2025.pdf", "Accessible_Travel_Policy.pdf", "Making_Rail_Accessible.pdf"]


def pytest_collection_modifyitems(config, items):
    missing = [f for f in NEEDED if not (RAW / f).exists()]
    if not missing:
        return
    skip = pytest.mark.skip(reason="source documents missing: run  python scripts/download_documents.py  first")
    for item in items:
        if item.fspath.basename in ("test_pipeline.py", "test_usecases.py", "test_user_stories.py"):
            item.add_marker(skip)
