"""FastAPI dependencies for staff and customer-session authorisation."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import TokenError
from app.auth.permissions import Permission, permissions_for

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class StaffPrincipal:
    user_id: str
    tenant_id: str
    roles: list[str]

    def can(self, perm: Permission) -> bool:
        return perm in permissions_for(self.roles)


@dataclass(frozen=True)
class SessionPrincipal:
    session_id: str
    tenant_id: str


def _container(request: Request):
    return request.app.state.container


async def current_staff(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> StaffPrincipal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        claims = _container(request).jwt.verify(creds.credentials, "access")
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    return StaffPrincipal(user_id=claims["sub"], tenant_id=claims["tenant_id"], roles=claims.get("roles", []))


def require(perm: Permission):
    async def dep(principal: StaffPrincipal = Depends(current_staff)) -> StaffPrincipal:
        if not principal.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission {perm.value}")
        return principal

    return dep


def session_from_token(request: Request, token: str) -> SessionPrincipal:
    try:
        claims = _container(request).jwt.verify(token, "session")
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    return SessionPrincipal(session_id=claims["sub"], tenant_id=claims["tenant_id"])


async def current_session(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> SessionPrincipal:
    """Customer-facing endpoints authenticate with the session token returned by POST /sessions."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing session token")
    return session_from_token(request, creds.credentials)
