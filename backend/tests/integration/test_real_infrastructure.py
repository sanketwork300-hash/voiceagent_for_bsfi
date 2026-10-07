"""Opt-in tests against real PostgreSQL / Redis / Elasticsearch (e.g. the docker compose stack).

    BFSI_INFRA_TESTS=1 pytest tests/integration/test_real_infrastructure.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.knowledge.embeddings.provider import HashingEmbeddings
from app.knowledge.ingestion.metadata import AccessLevel, DocumentMetadata
from app.knowledge.retrieval.base import IndexedChunk, SearchQuery
from app.knowledge.retrieval.hybrid import HybridRetriever
from app.knowledge.retrieval.reranker import LexicalReranker

pytestmark = pytest.mark.skipif(os.environ.get("BFSI_INFRA_TESTS") != "1", reason="set BFSI_INFRA_TESTS=1 with real services")
PG = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://bfsi:bfsi@localhost:5432/bfsi")
REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
ES = os.environ.get("TEST_ELASTICSEARCH_URL", "http://localhost:9200")


async def test_elasticsearch_hybrid_tenant_isolation_and_versions():
    from app.knowledge.retrieval.elasticsearch import ElasticsearchKnowledgeStore

    index = f"bfsi-test-{uuid.uuid4().hex[:8]}"
    store, emb = ElasticsearchKnowledgeStore(ES, index), HashingEmbeddings(64)
    await store.ensure_ready(64)
    try:
        async def put(tenant, doc, ver, text, **kw):
            m = DocumentMetadata(tenant_id=tenant, document_id=doc, version_id=ver, title="Fees", **kw)
            await store.index_chunks([IndexedChunk(f"{ver}:0", text, 1, None, (await emb.embed([text]))[0], m)])

        await put("t1", "fees", "v1", "ATM charge beyond free limit is Rs. 23 per transaction.", version="1")
        await put("t1", "fees", "v2", "ATM charge beyond free limit is Rs. 21 per transaction.", version="2")
        await put("t2", "fees2", "x1", "ATM charge beyond free limit is Rs. 99 per transaction.")
        await put("t1", "sop", "s1", "Internal SOP: waive ATM charge for senior staff.", access_level=AccessLevel.INTERNAL)
        await store.supersede_document("t1", "fees", keep_version_id="v2")
        hits = await HybridRetriever(store, emb, LexicalReranker()).search(SearchQuery(tenant_id="t1", text="ATM charge free limit"))
        assert [h.chunk_id for h in hits] == ["v2:0"]
        assert {h.metadata.tenant_id for h in hits} == {"t1"}
    finally:
        await store.es.indices.delete(index=index)
        await store.aclose()


async def test_redis_state_store_lock_and_pubsub():
    from app.sessions.manager import RedisStateStore

    s = RedisStateStore(REDIS)
    key = f"k-{uuid.uuid4().hex}"
    await s.set(key, "v", 30)
    assert await s.get(key) == "v"
    order: list[str] = []

    async def worker(name: str):
        async with s.lock(key, timeout=5):
            order.append(f"{name}-in")
            await asyncio.sleep(0.05)
            order.append(f"{name}-out")

    await asyncio.gather(worker("a"), worker("b"))
    assert order[1].endswith("-out") and order[2].endswith("-in")  # never interleaved
    got: list[dict] = []

    async def sub():
        async for m in s.subscribe(f"ch-{key}"):
            got.append(m)
            return

    t = asyncio.create_task(sub())
    await asyncio.sleep(0.1)
    await s.publish(f"ch-{key}", {"type": "ping"})
    await asyncio.wait_for(t, 3)
    assert got == [{"type": "ping"}]
    await s.aclose()


async def test_audit_chain_concurrent_appends_on_postgres():
    from app.database.session import Database
    from app.security.audit import AuditLogger

    db = Database(PG)
    tenant = f"audit-test-{uuid.uuid4().hex[:8]}"
    loggers = [AuditLogger(db), AuditLogger(db)]  # two loggers ~ two replicas (separate in-process locks)
    await asyncio.gather(*(loggers[i % 2].record(tenant, "test.event", payload={"i": i}) for i in range(40)))
    ok, broken = await loggers[0].verify_chain(tenant)
    assert ok, f"chain broken at {broken}"
    from sqlalchemy import func, select

    from app.database.models import AuditEvent

    async with db.session() as s:
        n = (await s.execute(select(func.count()).where(AuditEvent.tenant_id == tenant))).scalar_one()
    assert n == 40
    await db.dispose()
