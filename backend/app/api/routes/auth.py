from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import container

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    tenant: str
    email: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, c=Depends(container)) -> dict:
    found = await c.staff_auth.authenticate(body.tenant, body.email, body.password)
    if found is None:
        await c.audit.record("platform", "auth.staff_login_failed", actor_type="staff", outcome="failure",
                             payload={"tenant": body.tenant})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    user, tenant = found
    token = c.jwt.staff_token(user_id=user.id, tenant_id=tenant.id, roles=user.roles, ttl=c.settings.jwt_access_ttl_seconds)
    await c.audit.record(tenant.id, "auth.staff_login", actor_type="staff", actor_id=user.id)
    return {"access_token": token, "token_type": "bearer", "expires_in": c.settings.jwt_access_ttl_seconds,
            "tenant_id": tenant.id, "roles": user.roles}
