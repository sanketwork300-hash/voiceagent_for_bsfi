"""Document parsing: PDF, text, Markdown, HTML -> pages of text."""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field


@dataclass
class ParsedPage:
    number: int
    text: str


@dataclass
class ParsedDocument:
    title: str | None
    pages: list[ParsedPage] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


class UnsupportedDocumentError(ValueError):
    pass


def _clean(text: str) -> str:
    text = text.replace("\x00", "").replace("­", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)  # de-hyphenate line wraps
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_document(data: bytes, filename: str, content_type: str | None = None) -> ParsedDocument:
    name = filename.lower()
    if name.endswith(".pdf") or content_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [ParsedPage(i + 1, _clean(p.extract_text() or "")) for i, p in enumerate(reader.pages)]
        title = (reader.metadata.title if reader.metadata else None) or None
        if not any(p.text for p in pages):
            raise UnsupportedDocumentError("PDF has no extractable text (scanned PDFs need OCR before ingestion)")
        return ParsedDocument(title=title, pages=pages)
    if name.endswith((".txt", ".md", ".markdown")) or (content_type or "").startswith("text/plain"):
        text = data.decode("utf-8", errors="replace")
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), None)
        return ParsedDocument(title=title, pages=[ParsedPage(1, _clean(text))])
    if name.endswith((".html", ".htm")) or content_type == "text/html":
        raw = data.decode("utf-8", errors="replace")
        m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<(br|/p|/div|/h\d|/li)[^>]*>", "\n", raw, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        return ParsedDocument(title=m.group(1).strip() if m else None, pages=[ParsedPage(1, _clean(text))])
    raise UnsupportedDocumentError(f"unsupported document type: {filename}")
