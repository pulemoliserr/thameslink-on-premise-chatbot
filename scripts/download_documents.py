"""Download the four public source documents into data/raw/.

The PDFs are published by Govia Thameslink Railway and Rail Settlement Plan. They are not
redistributed in this repository, so fetch them from the publishers' websites:

    python scripts/download_documents.py
    python -m rag.ingest

If a publisher has replaced a document with a newer version, the script still saves it but
warns you. Then run the tests (python -m pytest -q): the app checks every rule's quoted
source text against the documents and shows which rules need updating.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
GTR = "https://www.thameslinkrailway.com/-/media/gtr/files/accessibility/atp-accessibility-documents"

# file name (as in data/documents.yaml) -> (URL, SHA-256 of the version this project was built and tested with)
DOCUMENTS = {
    "National_Rail_Conditions_of_Travel_2025.pdf": (
        "https://www.southeasternrailway.co.uk/-/media/goahead/southeastern/documents/policies/"
        "national_rail_conditions_of_travel_2025.pdf", "bf2c498e55968307fef924bb510b0da067170245145f4d05c45fd6871f6ba587"),
    "Accessible_Travel_Policy.pdf": (f"{GTR}/accessible-travel-policy.pdf", "ac3be028b992bb8fe876cc5d1fc642e26a97a6f957f526621be6018dd8c173b6"),
    "Making_Rail_Accessible.pdf": (f"{GTR}/making-rail-accessible.pdf", "f91160acb3804b64595b634b7f24c888d9ed59462a5602198e3d3133fe762073"),
    "Fleet_accessibility_information.pdf": (f"{GTR}/fleet-accessibility-information.pdf", "dfadda01438fe733cabfec86d35fac911c9cace3a6db182cda95adb30f9bc703"),
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (document downloader)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    problems = 0
    for name, (url, expected) in DOCUMENTS.items():
        target = RAW / name
        if target.exists() and "--force" not in sys.argv:
            print(f"  ok        {name} (already downloaded; use --force to fetch again)")
            continue
        try:
            data = fetch(url)
        except Exception as e:                      # network error, 404, ...
            print(f"  FAILED    {name}: {e}\n            Download it by hand from {url}\n"
                  f"            and save it as data/raw/{name}")
            problems += 1
            continue
        if not data.startswith(b"%PDF"):
            print(f"  FAILED    {name}: the server did not return a PDF. Download it by hand from {url}")
            problems += 1
            continue
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if digest == expected:
            print(f"  ok        {name}")
        else:
            print(f"  NEWER?    {name}: differs from the tested version. Run the tests to check the rules.")
    print("\nNext: python -m rag.ingest" if not problems else f"\n{problems} document(s) missing (see above).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
