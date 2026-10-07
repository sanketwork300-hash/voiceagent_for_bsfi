"""Citations for knowledge answers, and a cheap groundedness check used as a hallucination signal."""

from __future__ import annotations

import re
from typing import Any

from app.domain import SourceCitation
from app.knowledge.retrieval.base import ScoredChunk

_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def to_citations(chunks: list[ScoredChunk], snippet_chars: int = 700) -> list[SourceCitation]:
    return [
        SourceCitation(
            index=i + 1, document_id=c.metadata.document_id, title=c.metadata.title, version=c.metadata.version,
            page=c.page, section=c.section, chunk_id=c.chunk_id, score=round(c.score, 4), snippet=c.text[:snippet_chars],
        )
        for i, c in enumerate(chunks)
    ]


def for_llm(citations: list[SourceCitation]) -> list[dict[str, Any]]:
    return [
        {"index": c.index, "title": c.title, "version": c.version, "page": c.page, "section": c.section, "snippet": c.snippet}
        for c in citations
    ]


def cited_indices(answer: str) -> set[int]:
    out: set[int] = set()
    for m in _CITE.finditer(answer):
        out.update(int(x) for x in m.group(1).split(","))
    return out


def used_citations(answer: str, citations: list[SourceCitation]) -> list[SourceCitation]:
    idx = cited_indices(answer)
    return [c for c in citations if c.index in idx] or citations[:1]


def _norm_numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text) if len(n.replace(",", "")) >= 2}


def ungrounded_numbers(answer: str, evidence: list[str]) -> set[str]:
    """Numbers stated in the answer that appear in no source/tool output (likely hallucinated)."""
    stated = _norm_numbers(_CITE.sub("", answer))
    seen: set[str] = set()
    for e in evidence:
        seen |= _norm_numbers(e)
    # allow derived roundings like 1.5 vs 1.50 and percentages
    seen |= {s.rstrip("0").rstrip(".") for s in seen if "." in s}
    return {n for n in stated if n not in seen and n.rstrip("0").rstrip(".") not in seen}
