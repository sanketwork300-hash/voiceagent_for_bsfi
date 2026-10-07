from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, current_staff, require
from app.auth.permissions import Permission
from app.database.models import Tenant
from app.domain import new_id

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    name: str
    institution_type: str = "bank"
    default_language: str = "en"
    supported_languages: list[str] = ["en", "hi"]
    settings: dict = {}


def _view(t: Tenant) -> dict:
    return {"id": t.id, "slug": t.slug, "name": t.name, "institution_type": t.institution_type,
            "default_language": t.default_language, "supported_languages": t.supported_languages, "is_active": t.is_active}


@router.post("", status_code=201)
async def create_tenant(body: TenantCreate, p: StaffPrincipal = Depends(current_staff), c=Depends(container)) -> dict:
    if "platform_admin" not in p.roles:
        raise HTTPException(403, "platform_admin required")
    async with c.db.session() as s:
        if (await s.execute(select(Tenant).where(Tenant.slug == body.slug))).scalar_one_or_none():
            raise HTTPException(409, "slug already exists")
        t = Tenant(id=new_id(), **body.model_dump())
        s.add(t)
    await c.audit.record(t.id, "tenant.created", actor_type="staff", actor_id=p.user_id)
    return _view(t)


@router.get("/me")
async def my_tenant(p: StaffPrincipal = Depends(require(Permission.TENANT_READ)), c=Depends(container)) -> dict:
    async with c.db.session() as s:
        t = await s.get(Tenant, p.tenant_id)
    if t is None:
        raise HTTPException(404, "tenant not found")
    return _view(t)
