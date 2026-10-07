from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.config import Settings
from app.container import Container
from app.database.models import Tenant
from app.domain import Channel
from mock_bank import data as bank_data
from mock_bank.app import app as bank_app
from scripts.seed import seed_demo

ASSERTION_SECRET = "test-assertion-secret-0123456789abcdef"


def make_settings(tmp_path) -> Settings:
    return Settings(
        environment="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db", redis_url=None,
        knowledge_backend="memory", llm_provider="rule_based", llm_fallback_provider="none",
        document_storage_dir=str(tmp_path / "uploads"), mock_bank_url="http://mock-bank", otel_enabled=False,
        log_json=False, log_level="WARNING", customer_assertion_secret=ASSERTION_SECRET,
        jwt_secret="test-jwt-secret-0123456789abcdef0123", db_auto_create=True,
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def container(settings) -> AsyncIterator[Container]:
    bank_data.reset()
    c = Container(settings, http_transport=httpx.ASGITransport(app=bank_app))
    await c.startup(create_schema=True)
    await seed_demo(c)
    yield c
    await c.shutdown()


@pytest.fixture
async def tenant(container) -> Tenant:
    async with container.db.session() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "demo-bank"))).scalar_one()


def customer_assertion(customer_id: str = "CUST1001", tenant_slug: str = "demo-bank", amr: list[str] | None = None) -> str:
    return jwt.encode({"sub": customer_id, "aud": tenant_slug, "exp": int(time.time()) + 600, "amr": amr or ["pwd"]},
                      ASSERTION_SECRET, algorithm="HS256")


@pytest.fixture
def authed_session(container, tenant):
    async def make(channel: Channel = Channel.CHAT, customer_id: str = "CUST1001"):
        st = await container.sessions.create(tenant_id=tenant.id, channel=channel)
        async with container.sessions.locked(st.session_id, tenant.id) as s:
            await container.customer_auth.apply_assertion(s, customer_assertion(customer_id), "demo-bank")
        return await container.sessions.get(st.session_id, tenant.id)

    return make


@pytest.fixture
async def api(container) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app(container.settings, container=container)
    app.state.container = container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
