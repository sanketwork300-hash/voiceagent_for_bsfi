"""Ingestion: Document -> Parser -> Chunker -> Metadata -> Embedding -> Knowledge index."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.knowledge.embeddings.provider import EmbeddingProvider
from app.knowledge.ingestion.chunker import SectionChunker
from app.knowledge.ingestion.metadata import DocumentMetadata
from app.knowledge.ingestion.parser import parse_document
from app.knowledge.retrieval.base import IndexedChunk, KnowledgeStore
from app.security.redaction import RedactionProfile, redact

log = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    chunk_count: int
    page_count: int
    title: str | None


class IngestionPipeline:
    def __init__(self, store: KnowledgeStore, embeddings: EmbeddingProvider, chunker: SectionChunker | None = None) -> None:
        self.store = store
        self.embeddings = embeddings
        self.chunker = chunker or SectionChunker()

    async def ingest(self, data: bytes, filename: str, content_type: str | None, meta: DocumentMetadata) -> IngestionResult:
        parsed = parse_document(data, filename, content_type)
        chunks = self.chunker.chunk(parsed)
        # Knowledge documents must not carry customer PII; mask anything that slipped into a policy doc.
        texts = [redact(c.text, RedactionProfile.STORAGE) for c in chunks]
        vectors = await self.embeddings.embed(texts) if texts else []
        indexed = [
            IndexedChunk(
                chunk_id=f"{meta.version_id}:{c.index}",
                text=text,
                page=c.page,
                section=c.section,
                embedding=vec,
                metadata=meta,
            )
            for c, text, vec in zip(chunks, texts, vectors, strict=True)
        ]
        await self.store.supersede_document(meta.tenant_id, meta.document_id, keep_version_id=meta.version_id)
        await self.store.index_chunks(indexed)
        log.info("document indexed", extra={"document_id": meta.document_id, "chunks": len(indexed)})
        return IngestionResult(len(indexed), len(parsed.pages), parsed.title)
