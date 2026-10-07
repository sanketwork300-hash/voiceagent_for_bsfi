"""Rerankers: cross-encoder over HTTP (TEI / Cohere-compatible /rerank) or a lexical fallback."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

import httpx

from app.knowledge.retrieval.base import ScoredChunk

log = logging.getLogger(__name__)
_W = re.compile(r"\w+")


class Reranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, chunks: list[ScoredChunk], top_k: int) -> list[ScoredChunk]: ...


class NoopReranker(Reranker):
    async def rerank(self, query, chunks, top_k):
        return chunks[:top_k]


class LexicalReranker(Reranker):
    """Coverage + phrase-proximity + section-title match, blended with the fused retrieval score."""

    async def rerank(self, query, chunks, top_k):
        q = [w for w in _W.findall(query.lower()) if len(w) > 2]
        if not q or not chunks:
            return chunks[:top_k]
        max_fused = max(c.score for c in chunks) or 1.0
        bigrams = {f"{a} {b}" for a, b in zip(q, q[1:], strict=False)}
        for c in chunks:
            body = c.text.lower()
            words = set(_W.findall(body))
            stems = {w[:5] for w in words}
            coverage = sum(1.0 if w in words else 0.5 if w[:5] in stems else 0.0 for w in q) / len(q)
            phrase = sum(1 for bg in bigrams if bg in body) / max(1, len(bigrams))
            head = (c.section or "").lower() + " " + c.metadata.title.lower()
            heading = sum(1 for w in q if w[:5] in head) / len(q)
            c.retrievers["rerank"] = 0.45 * coverage + 0.25 * phrase + 0.15 * heading + 0.15 * (c.score / max_fused)
            c.score = c.retrievers["rerank"]
        return sorted(chunks, key=lambda c: -c.score)[:top_k]


class HTTPCrossEncoderReranker(Reranker):
    def __init__(self, url: str, fallback: Reranker | None = None) -> None:
        self.url = url
        self.fallback = fallback or LexicalReranker()
        self._client = httpx.AsyncClient(timeout=5)

    async def rerank(self, query, chunks, top_k):
        if not chunks:
            return []
        try:
            r = await self._client.post(self.url, json={"query": query, "texts": [c.text for c in chunks]})
            r.raise_for_status()
            for item in r.json():
                chunks[item["index"]].score = float(item["score"])
                chunks[item["index"]].retrievers["rerank"] = float(item["score"])
            return sorted(chunks, key=lambda c: -c.score)[:top_k]
        except (httpx.HTTPError, KeyError, IndexError):
            log.warning("cross-encoder reranker unavailable; using lexical fallback")
            return await self.fallback.rerank(query, chunks, top_k)
