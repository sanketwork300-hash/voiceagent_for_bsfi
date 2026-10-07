from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.knowledge.ingestion.metadata import AccessLevel, DocumentMetadata


@dataclass
class IndexedChunk:
    chunk_id: str
    text: str
    page: int
    section: str | None
    embedding: list[float]
    metadata: DocumentMetadata


@dataclass
class ScoredChunk:
    chunk_id: str
    text: str
    page: int
    section: str | None
    metadata: DocumentMetadata
    score: float
    retrievers: dict[str, float] = field(default_factory=dict)


@dataclass
class SearchQuery:
    """Retrieval request. `tenant_id` is mandatory and is enforced by every store implementation;
    caller-supplied filters can only narrow results further, never widen them."""

    tenant_id: str
    text: str
    top_k: int = 5
    candidate_k: int = 30
    product: str | None = None
    language: str | None = None
    region: str | None = None
    doc_type: str | None = None
    access_levels: tuple[AccessLevel, ...] = (AccessLevel.PUBLIC,)
    as_of: date = field(default_factory=date.today)

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required for knowledge retrieval")


class KnowledgeStore(ABC):
    @abstractmethod
    async def ensure_ready(self, dimensions: int) -> None: ...

    @abstractmethod
    async def index_chunks(self, chunks: list[IndexedChunk]) -> None: ...

    @abstractmethod
    async def supersede_document(self, tenant_id: str, document_id: str, keep_version_id: str) -> None: ...

    @abstractmethod
    async def delete_document(self, tenant_id: str, document_id: str) -> None: ...

    @abstractmethod
    async def lexical_search(self, q: SearchQuery) -> list[ScoredChunk]: ...

    @abstractmethod
    async def vector_search(self, q: SearchQuery, vector: list[float]) -> list[ScoredChunk]: ...

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:  # noqa: B027
        pass


def metadata_allows(meta: DocumentMetadata, q: SearchQuery) -> bool:
    """Reference implementation of the retrieval filter (mirrored as an ES bool filter)."""
    if meta.tenant_id != q.tenant_id or meta.status.value != "active":
        return False
    if meta.access_level not in q.access_levels:
        return False
    if meta.effective_from and meta.effective_from > q.as_of:
        return False
    if meta.effective_until and meta.effective_until < q.as_of:
        return False
    for attr in ("product", "region", "doc_type"):
        want = getattr(q, attr)
        if want and getattr(meta, attr) not in (want, None):
            return False
    return not (q.language and meta.language not in (q.language, "multi"))
