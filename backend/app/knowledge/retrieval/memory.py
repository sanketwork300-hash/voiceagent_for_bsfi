"""In-process knowledge store (BM25 + cosine). Used by tests and single-node dev without Elasticsearch."""

from __future__ import annotations

import math
import re
from collections import Counter

from app.knowledge.ingestion.metadata import DocumentStatus
from app.knowledge.retrieval.base import (
    IndexedChunk,
    KnowledgeStore,
    ScoredChunk,
    SearchQuery,
    metadata_allows,
)

_TOKEN = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if len(t) > 1]


class InMemoryKnowledgeStore(KnowledgeStore):
    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.chunks: dict[str, IndexedChunk] = {}
        self._tf: dict[str, Counter] = {}

    async def ensure_ready(self, dimensions: int) -> None:
        return None

    async def index_chunks(self, chunks: list[IndexedChunk]) -> None:
        for c in chunks:
            self.chunks[c.chunk_id] = c
            self._tf[c.chunk_id] = Counter(tokenize(f"{c.metadata.title} {c.section or ''} {c.text}"))

    async def supersede_document(self, tenant_id: str, document_id: str, keep_version_id: str) -> None:
        for c in self.chunks.values():
            m = c.metadata
            if m.tenant_id == tenant_id and m.document_id == document_id and m.version_id != keep_version_id:
                m.status = DocumentStatus.SUPERSEDED

    async def delete_document(self, tenant_id: str, document_id: str) -> None:
        for cid in [cid for cid, c in self.chunks.items()
                    if c.metadata.tenant_id == tenant_id and c.metadata.document_id == document_id]:
            self.chunks.pop(cid)
            self._tf.pop(cid)

    def _eligible(self, q: SearchQuery) -> list[IndexedChunk]:
        return [c for c in self.chunks.values() if metadata_allows(c.metadata, q)]

    async def lexical_search(self, q: SearchQuery) -> list[ScoredChunk]:
        docs = self._eligible(q)
        if not docs:
            return []
        terms = tokenize(q.text)
        n = len(docs)
        avgdl = sum(sum(self._tf[d.chunk_id].values()) for d in docs) / n
        df = Counter(t for d in docs for t in set(self._tf[d.chunk_id]) if t in terms)
        scored = []
        for d in docs:
            tf = self._tf[d.chunk_id]
            dl = sum(tf.values())
            s = 0.0
            for t in terms:
                if t in tf:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / avgdl))
            if s > 0:
                scored.append(ScoredChunk(d.chunk_id, d.text, d.page, d.section, d.metadata, s, {"bm25": s}))
        return sorted(scored, key=lambda c: -c.score)[: q.candidate_k]

    async def vector_search(self, q: SearchQuery, vector: list[float]) -> list[ScoredChunk]:
        scored = []
        for d in self._eligible(q):
            s = sum(a * b for a, b in zip(vector, d.embedding, strict=False))
            scored.append(ScoredChunk(d.chunk_id, d.text, d.page, d.section, d.metadata, s, {"knn": s}))
        return sorted(scored, key=lambda c: -c.score)[: q.candidate_k]
