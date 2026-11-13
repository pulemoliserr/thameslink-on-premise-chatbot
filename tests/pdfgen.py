"""Make small text PDFs for the tests (no extra library needed)."""
from __future__ import annotations


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[tuple[str, float, bool]]]) -> bytes:
    """pages: for each page, a list of (text, font size, bold) lines, written top to bottom."""
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    pages_id = len(objects) + 2 * len(pages) + 1           # reserved: content + page objects come first
    page_ids = []
    for lines in pages:
        y, ops = 800, ["BT"]
        for text, size, is_bold in lines:
            ops.append(f"/{'F2' if is_bold else 'F1'} {size} Tf 1 0 0 1 50 {y} Tm ({_esc(text)}) Tj")
            y -= size * 1.6
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                            f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"))
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>") == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def policy_pdf(topic_sentence: str, title: str = "Lost Property Policy") -> bytes:
    """A two-page policy document with headings and enough text to be indexed."""
    filler = ("Staff should record the item, the date and the station, and give the passenger a reference number. "
              "Items are kept safely and returned when the owner proves the item is theirs. ")
    return make_pdf([
        [(title, 24, True), ("1. Purpose", 14, True)]
        + [(s, 10, False) for s in _wrap(filler * 3)],
        [("2. Rules", 14, True)] + [(s, 10, False) for s in _wrap(topic_sentence + " " + filler * 2)],
    ])


def _wrap(text: str, width: int = 95) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + ([line] if line else [])
