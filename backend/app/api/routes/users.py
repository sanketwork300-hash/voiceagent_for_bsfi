from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import container
from app.auth.authentication import hash_password
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.database.models import User
from app.database.repositories import TenantRepository
from app.domain import new_id

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: str
    full_name: str = ""
    password: str
    roles: list[str]


@router.post("", status_code=201)
async def create_user(body: UserCreate, p: StaffPrincipal = Depends(require(Permission.USER_MANAGE)), c=Depends(container)) -> dict:
    unknown = set(body.roles) - set(ROLE_PERMISSIONS)
    if unknown or ("platform_admin" in body.roles and "platform_admin" not in p.roles):
        raise HTTPException(400, f"invalid roles: {sorted(unknown) or ['platform_admin']}")
    if len(body.password) < 12:
        raise HTTPException(400, "password must be at least 12 characters")
    async with c.db.session() as s:
        if (await s.execute(select(User).where(User.tenant_id == p.tenant_id, User.email == body.email.lower()))).scalar_one_or_none():
            raise HTTPException(409, "user exists")
        u = User(id=new_id(), tenant_id=p.tenant_id, email=body.email.lower(), full_name=body.full_name,
                 password_hash=hash_password(body.password), roles=body.roles)
        s.add(u)
    await c.audit.record(p.tenant_id, "user.created", actor_type="staff", actor_id=p.user_id, resource=u.id,
                         payload={"roles": body.roles})
    return {"id": u.id, "email": u.email, "roles": u.roles}


@router.get("")
async def list_users(p: StaffPrincipal = Depends(require(Permission.USER_MANAGE)), c=Depends(container)) -> list[dict]:
    async with c.db.session() as s:
        rows = await TenantRepository(s, User, p.tenant_id).list(order_by=User.created_at)
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "roles": u.roles, "is_active": u.is_active} for u in rows]
