"""Elasticsearch knowledge store: BM25 (multi-field) + dense_vector kNN, tenant-filtered at query time."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk

from app.knowledge.ingestion.metadata import (
    AccessLevel,
    DocumentMetadata,
    DocumentStatus,
)
from app.knowledge.retrieval.base import (
    IndexedChunk,
    KnowledgeStore,
    ScoredChunk,
    SearchQuery,
)

log = logging.getLogger(__name__)


def _mapping(dims: int) -> dict[str, Any]:
    kw = {"type": "keyword"}
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {"analyzer": {"bfsi_text": {"type": "custom", "tokenizer": "standard",
                                                    "filter": ["lowercase", "asciifolding", "porter_stem"]}}},
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "tenant_id": kw, "document_id": kw, "version_id": kw, "chunk_id": kw,
                "title": {"type": "text", "analyzer": "bfsi_text", "fields": {"raw": kw}},
                "section": {"type": "text", "analyzer": "bfsi_text"},
                "text": {"type": "text", "analyzer": "bfsi_text"},
                "page": {"type": "integer"},
                "doc_type": kw, "product": kw, "language": kw, "region": kw, "version": kw, "status": kw,
                "access_level": kw, "tags": kw, "source": kw,
                "effective_from": {"type": "date"}, "effective_until": {"type": "date"},
                "embedding": {"type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine"},
            },
        },
    }


class ElasticsearchKnowledgeStore(KnowledgeStore):
    def __init__(self, url: str, index: str, api_key: str | None = None) -> None:
        self.index = index
        self.es = AsyncElasticsearch(url, api_key=api_key, request_timeout=10)

    async def ensure_ready(self, dimensions: int) -> None:
        if not await self.es.indices.exists(index=self.index):
            body = _mapping(dimensions)
            await self.es.indices.create(index=self.index, settings=body["settings"], mappings=body["mappings"])
            log.info("created knowledge index", extra={"index": self.index})

    async def ping(self) -> bool:
        return bool(await self.es.ping())

    async def index_chunks(self, chunks: list[IndexedChunk]) -> None:
        def actions():
            for c in chunks:
                m = c.metadata
                yield {
                    "_index": self.index, "_id": c.chunk_id, "_routing": m.tenant_id,
                    "_source": {
                        "tenant_id": m.tenant_id, "document_id": m.document_id, "version_id": m.version_id,
                        "chunk_id": c.chunk_id, "title": m.title, "section": c.section, "text": c.text, "page": c.page,
                        "doc_type": m.doc_type, "product": m.product, "language": m.language, "region": m.region,
                        "version": m.version, "status": m.status.value, "access_level": m.access_level.value,
                        "tags": m.tags, "source": m.source,
                        "effective_from": m.effective_from.isoformat() if m.effective_from else None,
                        "effective_until": m.effective_until.isoformat() if m.effective_until else None,
                        "embedding": c.embedding,
                    },
                }

        await async_bulk(self.es, actions(), refresh="wait_for")

    async def supersede_document(self, tenant_id: str, document_id: str, keep_version_id: str) -> None:
        await self.es.update_by_query(
            index=self.index, routing=tenant_id, refresh=True, conflicts="proceed",
            query={"bool": {"filter": [{"term": {"tenant_id": tenant_id}}, {"term": {"document_id": document_id}}],
                            "must_not": [{"term": {"version_id": keep_version_id}}]}},
            script={"source": "ctx._source.status = params.s", "params": {"s": DocumentStatus.SUPERSEDED.value}},
        )

    async def delete_document(self, tenant_id: str, document_id: str) -> None:
        try:
            await self.es.delete_by_query(index=self.index, routing=tenant_id, refresh=True, query={
                "bool": {"filter": [{"term": {"tenant_id": tenant_id}}, {"term": {"document_id": document_id}}]}})
        except NotFoundError:
            pass

    @staticmethod
    def _filters(q: SearchQuery) -> list[dict[str, Any]]:
        today = q.as_of.isoformat()
        f: list[dict[str, Any]] = [
            {"term": {"tenant_id": q.tenant_id}},  # mandatory tenant isolation
            {"term": {"status": DocumentStatus.ACTIVE.value}},
            {"terms": {"access_level": [a.value for a in q.access_levels]}},
            {"bool": {"should": [{"bool": {"must_not": {"exists": {"field": "effective_from"}}}},
                                 {"range": {"effective_from": {"lte": today}}}]}},
            {"bool": {"should": [{"bool": {"must_not": {"exists": {"field": "effective_until"}}}},
                                 {"range": {"effective_until": {"gte": today}}}]}},
        ]
        for field_name in ("product", "region", "doc_type"):
            if val := getattr(q, field_name):
                f.append({"bool": {"should": [{"term": {field_name: val}},
                                              {"bool": {"must_not": {"exists": {"field": field_name}}}}]}})
        if q.language:
            f.append({"terms": {"language": [q.language, "multi"]}})
        return f

    def _hit(self, h: dict[str, Any], kind: str) -> ScoredChunk:
        s = h["_source"]
        meta = DocumentMetadata(
            tenant_id=s["tenant_id"], document_id=s["document_id"], version_id=s["version_id"], title=s["title"],
            doc_type=s.get("doc_type") or "policy", product=s.get("product"), language=s.get("language") or "en",
            region=s.get("region"), version=s.get("version") or "1", status=DocumentStatus(s["status"]),
            access_level=AccessLevel(s["access_level"]), tags=s.get("tags") or [], source=s.get("source"),
            effective_from=date.fromisoformat(s["effective_from"][:10]) if s.get("effective_from") else None,
            effective_until=date.fromisoformat(s["effective_until"][:10]) if s.get("effective_until") else None,
        )
        return ScoredChunk(s["chunk_id"], s["text"], s.get("page") or 1, s.get("section"), meta, h["_score"], {kind: h["_score"]})

    async def lexical_search(self, q: SearchQuery) -> list[ScoredChunk]:
        res = await self.es.search(
            index=self.index, routing=q.tenant_id, size=q.candidate_k, source_excludes=["embedding"],
            query={"bool": {"filter": self._filters(q), "must": [{"multi_match": {
                "query": q.text, "fields": ["title^2", "section^1.5", "text"], "type": "best_fields"}}]}},
        )
        return [self._hit(h, "bm25") for h in res["hits"]["hits"]]

    async def vector_search(self, q: SearchQuery, vector: list[float]) -> list[ScoredChunk]:
        res = await self.es.search(
            index=self.index, routing=q.tenant_id, size=q.candidate_k, source_excludes=["embedding"],
            knn={"field": "embedding", "query_vector": vector, "k": q.candidate_k,
                 "num_candidates": max(100, q.candidate_k * 4), "filter": self._filters(q)},
        )
        return [self._hit(h, "knn") for h in res["hits"]["hits"]]

    async def aclose(self) -> None:
        await self.es.close()
