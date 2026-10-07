from datetime import date, timedelta

from app.knowledge.citations import ungrounded_numbers, used_citations
from app.knowledge.embeddings.provider import HashingEmbeddings
from app.knowledge.ingestion.chunker import SectionChunker
from app.knowledge.ingestion.metadata import (
    AccessLevel,
    DocumentMetadata,
    DocumentStatus,
)
from app.knowledge.ingestion.parser import ParsedDocument, ParsedPage
from app.knowledge.retrieval.base import IndexedChunk, ScoredChunk, SearchQuery
from app.knowledge.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from app.knowledge.retrieval.memory import InMemoryKnowledgeStore
from app.knowledge.retrieval.reranker import LexicalReranker


def meta(tenant="t1", doc="d1", ver="v1", **kw):
    return DocumentMetadata(tenant_id=tenant, document_id=doc, version_id=ver, title=kw.pop("title", "Doc"), **kw)


async def _index(store, emb, text, **kw):
    m = meta(**kw)
    await store.index_chunks([IndexedChunk(f"{m.version_id}:0", text, 1, None, (await emb.embed([text]))[0], m)])


def test_chunker_keeps_sections():
    doc = ParsedDocument("x", [ParsedPage(1, "1. Fees\nA processing fee of 0.5% applies to all loans.\n2. Charges\nLate payment costs Rs. 500 per instance.")])
    chunks = SectionChunker().chunk(doc)
    assert [c.section for c in chunks] == ["1. Fees", "2. Charges"]
    assert chunks[0].text.startswith("1. Fees")


def test_rrf_fuses_rankings():
    m = meta()
    a, b, c = (ScoredChunk(x, x, 1, None, m, 1.0) for x in "abc")
    fused = reciprocal_rank_fusion({"bm25": [a, b], "knn": [b, c]})
    assert fused[0].chunk_id == "b"


async def test_hybrid_retrieval_tenant_isolation_and_validity():
    store, emb = InMemoryKnowledgeStore(), HashingEmbeddings(128)
    await _index(store, emb, "Home loan foreclosure charges are nil for floating rate loans.", tenant="t1", doc="a", ver="a1")
    await _index(store, emb, "Home loan foreclosure charges are 4% for everybody.", tenant="t2", doc="b", ver="b1")
    await _index(store, emb, "Old foreclosure policy: 3% charge.", tenant="t1", doc="c", ver="c1",
                 effective_until=date.today() - timedelta(days=1))
    await _index(store, emb, "Internal SOP: foreclosure waiver approvals.", tenant="t1", doc="e", ver="e1", access_level=AccessLevel.INTERNAL)
    r = HybridRetriever(store, emb, LexicalReranker())
    hits = await r.search(SearchQuery(tenant_id="t1", text="home loan foreclosure charges"))
    assert hits and {h.metadata.tenant_id for h in hits} == {"t1"}
    assert {h.metadata.document_id for h in hits} == {"a"}


async def test_superseded_versions_excluded():
    store, emb = InMemoryKnowledgeStore(), HashingEmbeddings(128)
    await _index(store, emb, "Processing fee is 1%.", doc="d", ver="v1", version="1")
    await _index(store, emb, "Processing fee is 0.5%.", doc="d", ver="v2", version="2")
    await store.supersede_document("t1", "d", keep_version_id="v2")
    hits = await HybridRetriever(store, emb, LexicalReranker()).search(SearchQuery(tenant_id="t1", text="processing fee"))
    assert [h.metadata.version_id for h in hits] == ["v2"]
    assert store.chunks["v1:0"].metadata.status == DocumentStatus.SUPERSEDED


def test_search_query_requires_tenant():
    import pytest

    with pytest.raises(ValueError):
        SearchQuery(tenant_id="", text="x")


def test_grounding_check():
    assert ungrounded_numbers("Charge is 2% [1]", ["a charge of 2% applies"]) == set()
    assert ungrounded_numbers("Your balance is ₹99,999", ['{"available_balance": 485230.75}']) == {"99999"}


def test_used_citations():
    from app.domain import SourceCitation

    cs = [SourceCitation(index=i, document_id="d", title="t", chunk_id=str(i), score=1, snippet="s") for i in (1, 2)]
    assert [c.index for c in used_citations("see [2]", cs)] == [2]
