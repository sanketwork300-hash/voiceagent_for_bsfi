from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import Integration

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationCreate(BaseModel):
    name: str
    kind: str  # rest | openapi | mcp | adapter
    base_url: str | None = None
    auth_type: str = "none"
    credentials: dict[str, Any] | None = None  # encrypted at rest; may use env:/vault: references
    config: dict[str, Any] = {}


@router.post("", status_code=201)
async def create_integration(body: IntegrationCreate, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)),
                             c=Depends(container)) -> dict:
    row = await c.integrations.create(p.tenant_id, **body.model_dump())
    await c.audit.record(p.tenant_id, "integration.created", actor_type="staff", actor_id=p.user_id, resource=row.id,
                         payload={"kind": row.kind, "base_url": row.base_url})
    return {"id": row.id, "name": row.name, "kind": row.kind, "status": row.status}


@router.get("")
async def list_integrations(p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)), c=Depends(container)) -> list[dict]:
    async with c.db.session() as s:
        rows = (await s.execute(select(Integration).where(Integration.tenant_id == p.tenant_id))).scalars().all()
    return [{"id": r.id, "name": r.name, "kind": r.kind, "base_url": r.base_url, "auth_type": r.auth_type, "status": r.status,
             "last_checked_at": r.last_checked_at, "is_enabled": r.is_enabled} for r in rows]


@router.post("/{integration_id}/test")
async def test_integration(integration_id: str, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)),
                           c=Depends(container)) -> dict:
    return await c.integrations.test(p.tenant_id, integration_id)


class OpenAPIImport(BaseModel):
    spec: dict[str, Any] | None = None  # omit to fetch {base_url}/openapi.json
    only: list[str] | None = None
    enable: bool = False  # imported tools start disabled until reviewed


@router.post("/{integration_id}/import-openapi")
async def import_openapi(integration_id: str, body: OpenAPIImport, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)),
                         c=Depends(container)) -> dict:
    tools = await c.integrations.import_openapi(p.tenant_id, integration_id, body.spec, set(body.only) if body.only else None, body.enable)
    await c.audit.record(p.tenant_id, "integration.openapi_imported", actor_type="staff", actor_id=p.user_id,
                         resource=integration_id, payload={"tools": [t.name for t in tools]})
    return {"imported": [t.llm_view() | {"min_auth_state": t.min_auth_state, "internal": t.internal} for t in tools]}
