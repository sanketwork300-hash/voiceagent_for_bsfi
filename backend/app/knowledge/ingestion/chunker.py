"""Section-aware chunking that keeps headings, clause numbers and page references with each chunk."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.knowledge.ingestion.parser import ParsedDocument

_HEADING = re.compile(r"^(?:#{1,6}\s+.+|(?:\d+(?:\.\d+)*\.?)\s+[A-Z][^\n]{2,80}|[A-Z][A-Z0-9 &/,()-]{4,80})$")


@dataclass
class Chunk:
    index: int
    text: str
    page: int
    section: str | None
    char_count: int


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class SectionChunker:
    def __init__(self, max_tokens: int = 220, overlap_tokens: int = 40) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, doc: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        section: str | None = None
        buf: list[str] = []
        buf_page = 1

        def flush() -> None:
            nonlocal buf
            text = " ".join(b.strip() for b in buf).strip()
            if text:
                body = f"{section}\n{text}" if section and not text.startswith(section) else text
                chunks.append(Chunk(len(chunks), body, buf_page, section, len(body)))
            tail = " ".join(" ".join(buf).split()[-self.overlap_tokens // 2 :]) if buf else ""
            buf = [tail] if tail and self.overlap_tokens else []

        for page in doc.pages:
            for para in re.split(r"\n\s*\n|\n(?=\S)", page.text):
                para = para.strip()
                if not para:
                    continue
                if _HEADING.match(para) and len(para) < 90:
                    if buf and any(b.strip() for b in buf):
                        flush()
                        buf = []
                    section = para.lstrip("# ").strip()
                    buf_page = page.number
                    continue
                if not buf or not any(b.strip() for b in buf):
                    buf_page = page.number
                if _approx_tokens(" ".join(buf) + para) > self.max_tokens and buf:
                    flush()
                    buf_page = page.number
                for piece in self._split_long(para):
                    buf.append(piece)
                    if _approx_tokens(" ".join(buf)) > self.max_tokens:
                        flush()
        if buf and any(b.strip() for b in buf):
            flush()
        return [c for c in chunks if len(c.text) > 30]

    def _split_long(self, para: str) -> list[str]:
        if _approx_tokens(para) <= self.max_tokens:
            return [para]
        sentences = re.split(r"(?<=[.!?;])\s+", para)
        out, cur = [], ""
        for s in sentences:
            if _approx_tokens(cur + " " + s) > self.max_tokens and cur:
                out.append(cur.strip())
                cur = ""
            cur += " " + s
        if cur.strip():
            out.append(cur.strip())
        return out
