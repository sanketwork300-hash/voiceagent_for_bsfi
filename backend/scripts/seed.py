"""Seed the demo tenant: users, agent, mock-bank integrations (OpenAPI + MCP), policies, sample documents.

Usage:  python -m scripts.seed           (uses .env; the mock bank must be reachable at MOCK_BANK_URL)
Idempotent: re-running leaves an existing demo tenant untouched.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from app.auth.authentication import hash_password
from app.container import Container
from app.database.models import Agent, Tenant, User
from app.domain import new_id

log = logging.getLogger("seed")
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_documents"
DEMO_SLUG = "demo-bank"
DEMO_PASSWORD = "DemoBank!2026secure"
SAMPLE_DOCS = {
    "home_loan_policy.pdf": {"product": "home_loan", "doc_type": "policy", "version": "3.2"},
    "credit_card_terms.pdf": {"product": "credit_card", "doc_type": "terms", "version": "2026.1"},
    "fees.pdf": {"product": None, "doc_type": "fees", "version": "2026.04"},
    "savings_account_faq.pdf": {"product": "savings_account", "doc_type": "faq", "version": "2026.03"},
}


async def seed_tenant(c: Container, *, slug: str, name: str, mock_bank_url: str, api_key: str,
                      with_documents: bool = True) -> Tenant:
    async with c.db.session() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if existing:
        return existing
    tenant = Tenant(id=new_id(), slug=slug, name=name, institution_type="bank", default_language="en",
                    supported_languages=["en", "hi", "mr", "ta", "te", "bn", "kn", "gu", "pa", "ml"],
                    # inbound numbers come from configuration (DEMO_SIP_NUMBERS), never from code
                    settings={"sip_numbers": list(c.settings.demo_sip_numbers)})
    async with c.db.session() as s:
        s.add(tenant)
        await s.flush()  # parent row first: users/agents reference it
        for email, roles in ((f"admin@{slug}.example", ["tenant_admin"]), (f"supervisor@{slug}.example", ["supervisor"]),
                             (f"agent@{slug}.example", ["human_agent"]), (f"auditor@{slug}.example", ["auditor"])):
            s.add(User(id=new_id(), tenant_id=tenant.id, email=email, full_name=email.split("@")[0].title(),
                       password_hash=hash_password(DEMO_PASSWORD), roles=roles))
        s.add(Agent(id=new_id(), tenant_id=tenant.id, name="Aarya", description="Retail banking assistant (chat + voice)",
                    persona_prompt="You are warm, concise and precise, like an experienced relationship manager.",
                    channels=["chat", "voice"], languages=["en", "hi", "mr", "ta", "te", "bn", "kn", "gu", "pa", "ml"],
                    voice_config={}))
    creds = {"api_key": api_key, "header": "X-API-Key"}
    rest = await c.integrations.create(tenant.id, name="Mock Bank Core API (OpenAPI)", kind="openapi",
                                       base_url=mock_bank_url, auth_type="api_key", credentials=creds)
    await c.integrations.import_openapi(tenant.id, rest.id, enable=True)
    mcp = await c.integrations.create(tenant.id, name="Mock Bank Cards & Payments (MCP)", kind="mcp",
                                      base_url=f"{mock_bank_url.rstrip('/')}/mcp", auth_type="api_key", credentials=creds)
    await c.integrations.register_mcp_server(tenant.id, name="mock-bank-mcp", url=f"{mock_bank_url.rstrip('/')}/mcp",
                                             integration_id=mcp.id, auto_enable=True)
    if with_documents:
        for fname, meta in SAMPLE_DOCS.items():
            path = SAMPLE_DIR / fname
            if not path.exists():
                log.warning("sample document missing: %s (run python -m scripts.generate_sample_docs)", fname)
                continue
            await c.documents.upload(tenant.id, data=path.read_bytes(), filename=fname, content_type="application/pdf",
                                     product=meta["product"], doc_type=meta["doc_type"], version=meta["version"])
    return tenant


async def seed_demo(c: Container) -> Tenant:
    return await seed_tenant(c, slug=DEMO_SLUG, name="Demo Bank", mock_bank_url=c.settings.mock_bank_url,
                             api_key=c.settings.mock_bank_api_key)


async def _main() -> None:
    from app.config import get_settings
    from app.observability.logging import setup_logging

    settings = get_settings()
    setup_logging("INFO", json_logs=False)
    c = Container(settings)
    await c.startup(create_schema=True)
    t = await seed_demo(c)
    print(f"tenant: {t.slug} ({t.id})")
    print(f"staff logins (password {DEMO_PASSWORD}): admin@, supervisor@, agent@, auditor@{t.slug}.example")
    await c.shutdown()


if __name__ == "__main__":
    asyncio.run(_main())
