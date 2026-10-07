"""Hybrid retrieval: BM25 + dense kNN fused with Reciprocal Rank Fusion, then reranked."""

from __future__ import annotations

import asyncio

from app.knowledge.embeddings.provider import EmbeddingProvider
from app.knowledge.retrieval.base import KnowledgeStore, ScoredChunk, SearchQuery
from app.knowledge.retrieval.reranker import Reranker


def reciprocal_rank_fusion(result_sets: dict[str, list[ScoredChunk]], k: int = 60,
                           weights: dict[str, float] | None = None) -> list[ScoredChunk]:
    fused: dict[str, ScoredChunk] = {}
    scores: dict[str, float] = {}
    for name, results in result_sets.items():
        w = (weights or {}).get(name, 1.0)
        for rank, chunk in enumerate(results):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + w / (k + rank + 1)
            if chunk.chunk_id in fused:
                fused[chunk.chunk_id].retrievers.update(chunk.retrievers)
            else:
                fused[chunk.chunk_id] = chunk
    for cid, c in fused.items():
        c.score = scores[cid]
        c.retrievers["rrf"] = scores[cid]
    return sorted(fused.values(), key=lambda c: -c.score)


class HybridRetriever:
    def __init__(self, store: KnowledgeStore, embeddings: EmbeddingProvider, reranker: Reranker,
                 weights: dict[str, float] | None = None) -> None:
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker
        self.weights = weights or {"bm25": 1.0, "knn": 1.0}

    async def search(self, q: SearchQuery) -> list[ScoredChunk]:
        vector = await self.embeddings.embed_query(q.text)
        lexical, dense = await asyncio.gather(self.store.lexical_search(q), self.store.vector_search(q, vector))
        fused = reciprocal_rank_fusion({"bm25": lexical, "knn": dense}, weights=self.weights)
        fused = _prefer_latest_versions(fused)
        return await self.reranker.rerank(q.text, fused[: q.candidate_k], q.top_k)


def _prefer_latest_versions(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
    """Defence in depth: if two versions of one document are both active, keep only the newest."""
    latest: dict[str, str] = {}
    for c in chunks:
        m = c.metadata
        cur = latest.get(m.document_id)
        if cur is None or _version_key(m.version) > _version_key(cur):
            latest[m.document_id] = m.version
    return [c for c in chunks if c.metadata.version == latest[c.metadata.document_id]]


def _version_key(v: str) -> tuple:
    return tuple((int(p), "") if p.isdigit() else (-1, p) for p in v.lower().lstrip("v").split("."))
