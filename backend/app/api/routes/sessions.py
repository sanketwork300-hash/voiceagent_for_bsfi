"""Customer sessions (shared by chat and voice) and customer authentication steps."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import container, ensure_session_access, load_session
from app.auth.authorization import SessionPrincipal, current_session
from app.auth.jwt import TokenError
from app.database.models import Agent, Tenant
from app.domain import AuthMethod, Channel

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    tenant: str = Field(description="Tenant slug")
    agent_id: str | None = None
    channel: Channel = Channel.CHAT
    language: str | None = None
    customer_assertion: str | None = Field(default=None, description="JWT from the institution's IdP after app/netbanking login")


@router.post("", status_code=201)
async def create_session(body: SessionCreate, request: Request, c=Depends(container)) -> dict:
    async with c.db.session() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == body.tenant, Tenant.is_active.is_(True)))).scalar_one_or_none()
        if tenant is None:
            raise HTTPException(404, "tenant not found")
        if body.agent_id:
            agent = (await s.execute(select(Agent).where(Agent.tenant_id == tenant.id, Agent.id == body.agent_id))).scalar_one_or_none()
            if agent is None or body.channel.value not in agent.channels:
                raise HTTPException(404, "agent not available on this channel")
    state = await c.sessions.create(tenant_id=tenant.id, channel=body.channel, agent_id=body.agent_id,
                                    language=body.language or tenant.default_language)
    if body.customer_assertion:
        async with c.sessions.locked(state.session_id, tenant.id) as st:
            try:
                await c.customer_auth.apply_assertion(st, body.customer_assertion, tenant.slug)
            except TokenError as e:
                raise HTTPException(401, str(e)) from e
            state = st
    token = c.jwt.session_token(session_id=state.session_id, tenant_id=tenant.id, ttl=c.settings.session_token_ttl_seconds)
    return {"session": state.public_view(), "session_token": token, "expires_in": c.settings.session_token_ttl_seconds}


@router.get("/{session_id}")
async def get_session(session_id: str, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    ensure_session_access(p, session_id)
    return (await load_session(c, session_id, p.tenant_id)).public_view()


class AssertionBody(BaseModel):
    assertion: str


@router.post("/{session_id}/auth/assertion")
async def apply_assertion(session_id: str, body: AssertionBody, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    ensure_session_access(p, session_id)
    async with c.db.session() as s:
        tenant = await s.get(Tenant, p.tenant_id)
    async with c.sessions.locked(session_id, p.tenant_id) as st:
        try:
            out = await c.customer_auth.apply_assertion(st, body.assertion, tenant.slug)
        except TokenError as e:
            raise HTTPException(401, str(e)) from e
        view = st.public_view()
    return {"success": out.success, "session": view}


class IdentifyBody(BaseModel):
    phone: str | None = None
    customer_ref: str | None = None


@router.post("/{session_id}/auth/identify")
async def identify(session_id: str, body: IdentifyBody, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    ensure_session_access(p, session_id)
    async with c.sessions.locked(session_id, p.tenant_id) as st:
        out = await c.customer_auth.identify(st, phone=body.phone, customer_id=body.customer_ref, method=AuthMethod.KNOWLEDGE)
        view = st.public_view()
    return {"success": out.success, "message": out.message, "session": view}


@router.post("/{session_id}/auth/otp/send")
async def send_otp(session_id: str, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    ensure_session_access(p, session_id)
    async with c.sessions.locked(session_id, p.tenant_id) as st:
        out = await c.customer_auth.start_otp(st, purpose="login")
    if not out.success:
        raise HTTPException(400, out.message)
    return {"sent": True, "destination": out.masked_destination}


class OTPBody(BaseModel):
    otp: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


@router.post("/{session_id}/auth/otp/verify")
async def verify_otp(session_id: str, body: OTPBody, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    """Out-of-band OTP entry. If a pending action is waiting for it, send any chat message (or use the
    WebSocket `auth.otp` frame) to resume the conversation."""
    ensure_session_access(p, session_id)
    async with c.sessions.locked(session_id, p.tenant_id) as st:
        out = await c.customer_auth.verify_otp(st, body.otp)
        view = st.public_view()
    return {"success": out.success, "locked": out.locked, "session": view}


@router.post("/{session_id}/close")
async def close(session_id: str, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    ensure_session_access(p, session_id)
    await c.sessions.close(session_id, p.tenant_id)
    return {"closed": True}
