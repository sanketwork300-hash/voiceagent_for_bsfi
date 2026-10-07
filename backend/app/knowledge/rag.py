"""RAG engine: authorized, tenant-isolated document knowledge for the agent runtime."""

from __future__ import annotations

import time

from app.domain import AuthState, SourceCitation
from app.knowledge.citations import to_citations
from app.knowledge.ingestion.metadata import AccessLevel
from app.knowledge.retrieval.base import SearchQuery
from app.knowledge.retrieval.hybrid import HybridRetriever
from app.observability import metrics
from app.observability.tracing import span
from app.security.injection import looks_like_injection, neutralize


class RAGEngine:
    def __init__(self, retriever: HybridRetriever, top_k: int = 5, candidate_k: int = 30, min_score: float = 0.12) -> None:
        self.retriever = retriever
        self.top_k = top_k
        self.candidate_k = candidate_k
        self.min_score = min_score

    @staticmethod
    def access_levels_for(auth_state: AuthState) -> tuple[AccessLevel, ...]:
        # INTERNAL documents (SOPs, scripts) are never retrievable by a customer-facing agent.
        if auth_state.satisfies(AuthState.IDENTIFIED):
            return (AccessLevel.PUBLIC, AccessLevel.CUSTOMER)
        return (AccessLevel.PUBLIC,)

    async def search(
        self,
        *,
        tenant_id: str,
        query: str,
        auth_state: AuthState = AuthState.UNAUTHENTICATED,
        product: str | None = None,
        doc_type: str | None = None,
        region: str | None = None,
        channel: str = "unknown",
        top_k: int | None = None,
        include_internal: bool = False,
    ) -> list[SourceCitation]:
        levels = self.access_levels_for(auth_state) + ((AccessLevel.INTERNAL,) if include_internal else ())
        q = SearchQuery(tenant_id=tenant_id, text=query, top_k=top_k or self.top_k, candidate_k=self.candidate_k,
                        product=product, doc_type=doc_type, region=region, access_levels=levels)
        started = time.perf_counter()
        with span("rag.search", **{"tenant.id": tenant_id, "rag.top_k": q.top_k}):
            chunks = await self.retriever.search(q)
        metrics.rag_latency.labels(channel).observe(time.perf_counter() - started)
        chunks = [c for c in chunks if c.retrievers.get("rerank", c.score) >= self.min_score]
        for c in chunks:
            if looks_like_injection(c.text):
                c.text = neutralize(c.text)
        metrics.rag_results.labels(channel).observe(len(chunks))
        return to_citations(chunks)
