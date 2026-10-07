from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.auth.authorization import SessionPrincipal, StaffPrincipal
from app.container import Container
from app.sessions.manager import SessionNotFound


def container(request: Request) -> Container:
    return request.app.state.container


async def load_session(c: Container, session_id: str, tenant_id: str):
    try:
        return await c.sessions.get(session_id, tenant_id)
    except SessionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found") from e


def ensure_session_access(principal: SessionPrincipal, session_id: str) -> None:
    if principal.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


def same_tenant(principal: StaffPrincipal, tenant_id: str) -> None:
    if principal.tenant_id != tenant_id and "platform_admin" not in principal.roles:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
