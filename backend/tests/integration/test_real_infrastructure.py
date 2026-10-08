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


# ------------------------------------------------------------------------------------------- multi-worker (Redis + Postgres)
@pytest.fixture
async def fresh_pg():
    """A throwaway database on the same server, so the test never touches the dev stack's data."""
    import asyncpg

    base = PG.replace("postgresql+asyncpg://", "postgresql://")
    name = f"bfsi_workers_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(base)
    await conn.execute(f'CREATE DATABASE "{name}"')
    await conn.close()
    yield PG.rsplit("/", 1)[0] + "/" + name
    conn = await asyncpg.connect(base)
    await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await conn.close()


async def test_redis_versioned_save_rejects_stale_writer():
    from app.sessions.manager import RedisStateStore

    s = RedisStateStore(REDIS)
    key = f"session:cas-{uuid.uuid4().hex}"
    assert await s.set_versioned(key, "v1", 30, 0)
    assert await s.set_versioned(key, "v2", 30, 1)
    assert not await s.set_versioned(key, "stale", 30, 1)  # a worker that lost its lock cannot clobber
    assert await s.get(key) == "v2"
    await s.aclose()


async def test_redis_lock_is_renewed_and_busy_requests_time_out():
    from app.sessions.manager import RedisStateStore, SessionBusy

    a, b = RedisStateStore(REDIS), RedisStateStore(REDIS)  # two workers, separate connections
    key = f"session:lock-{uuid.uuid4().hex}"
    async with a.lock(key, timeout=1.0, wait=1.0):
        await asyncio.sleep(2.5)  # longer than the TTL: renewal keeps it ours
        with pytest.raises(SessionBusy):
            async with b.lock(key, timeout=1.0, wait=0.3):
                pass
    async with b.lock(key, timeout=1.0, wait=1.0):  # released -> the other worker gets it at once
        pass
    await a.aclose()
    await b.aclose()


async def test_three_workers_on_redis_and_postgres(fresh_pg, tmp_path):
    import httpx

    from app.config import Settings
    from app.container import Container
    from app.domain import AgentRequest, Channel
    from mock_bank import data as bank
    from mock_bank.app import app as bank_app
    from scripts.seed import seed_demo
    from tests.conftest import customer_assertion, make_settings

    bank.reset()
    settings = Settings(**{**make_settings(tmp_path).model_dump(), "database_url": fresh_pg, "redis_url": REDIS})
    workers = [Container(settings, http_transport=httpx.ASGITransport(app=bank_app)) for _ in range(3)]
    await workers[0].startup(create_schema=True)
    tenant = await seed_demo(workers[0])
    for w in workers[1:]:
        await w.startup()

    async def say(w, st, text):
        return await w.runtime.process(AgentRequest(tenant_id=tenant.id, session_id=st.session_id, channel=Channel.VOICE, message=text))

    try:
        callers = []
        for cust in ("CUST1001", "CUST1002", "CUST1001"):
            st = await workers[0].sessions.create(tenant_id=tenant.id, channel=Channel.VOICE)
            async with workers[0].sessions.locked(st.session_id, tenant.id) as s:
                await workers[0].customer_auth.apply_assertion(s, customer_assertion(cust), "demo-bank")
            callers.append(st)
        # Callers A/B/C on Workers 1/2/3 at the same time: isolated
        out = await asyncio.gather(*(say(w, st, "What is my account balance?") for w, st in zip(workers, callers, strict=True)))
        assert "₹12,85,230.75" in out[0].text and "₹15,200" in out[1].text and "₹15,200" not in out[0].text
        # one session hit by 9 concurrent requests spread over all three workers: serialized by the Redis lock
        a = callers[0]
        v0 = (await workers[0].sessions.get(a.session_id, tenant.id)).version
        await asyncio.gather(*(say(workers[i % 3], a, f"What is my account balance? {i}") for i in range(9)))
        assert (await workers[1].sessions.get(a.session_id, tenant.id)).version == v0 + 9
        # a transfer that moves across workers mid-flow executes exactly once, verified
        assert (await say(workers[0], a, "Transfer ₹1,000 to Rahul")).pending_action.decision == "REQUIRE_AUTH"
        assert (await say(workers[1], a, "123456")).pending_action.decision == "REQUIRE_CONFIRMATION"
        r = await say(workers[2], a, "yes")
        assert "Transfer successful" in r.text and bank.DB["transfer_executions"] == 1
        assert (await say(workers[0], a, "yes")).pending_action is None and bank.DB["transfer_executions"] == 1
    finally:
        for w in workers:
            await w.shutdown()


async def test_capacity_pools_are_cluster_wide_on_redis():
    import redis.asyncio as redis

    from app.agents.execution.concurrency import SlotPool

    r1, r2 = redis.from_url(REDIS, decode_responses=True), redis.from_url(REDIS, decode_responses=True)
    prefix = f"cap-{uuid.uuid4().hex[:8]}"
    worker_a, worker_b = SlotPool(r1, prefix=prefix), SlotPool(r2, prefix=prefix)  # two workers, one Redis
    t1 = await worker_a.try_acquire("calls", 2, ttl=30)
    t2 = await worker_b.try_acquire("calls", 2, ttl=30)
    assert t1 and t2 and await worker_a.try_acquire("calls", 2, ttl=30) is None  # MAX_ACTIVE_CALLS across workers
    assert await worker_b.renew("calls", t2, ttl=60) and await worker_a.in_use("calls") == 2
    await worker_a.release("calls", t1)
    assert await worker_b.try_acquire("calls", 2, ttl=30) is not None
    short = await worker_a.try_acquire("llm", 1, ttl=0.2)  # a crashed holder's lease expires by itself
    await asyncio.sleep(0.4)
    assert short and await worker_b.try_acquire("llm", 1, ttl=5) is not None
    await r1.aclose()
    await r2.aclose()
