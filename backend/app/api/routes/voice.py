"""Voice sessions. Voice attaches to the same shared session as chat, so a customer can switch mid-conversation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import container, load_session
from app.auth.authorization import SessionPrincipal, current_session, session_from_token
from app.database.models import Tenant
from app.domain import Channel

router = APIRouter(prefix="/voice", tags=["voice"])
_optional_bearer = HTTPBearer(auto_error=False)


class VoiceSessionCreate(BaseModel):
    tenant: str | None = None  # required only when starting a brand-new session
    language: str | None = None


@router.post("/session", status_code=201)
async def voice_session(body: VoiceSessionCreate, request: Request, c=Depends(container),
                        creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer)) -> dict:
    """With a session token: switch that session (e.g. an ongoing chat) to voice.
    Without: start a new voice session for `tenant`."""
    if creds is not None:
        principal = session_from_token(request, creds.credentials)
        session_id, tenant_id = principal.session_id, principal.tenant_id
        await load_session(c, session_id, tenant_id)
        token = creds.credentials
    else:
        if not body.tenant:
            raise HTTPException(400, "tenant is required to start a new session")
        async with c.db.session() as s:
            tenant = (await s.execute(select(Tenant).where(Tenant.slug == body.tenant, Tenant.is_active.is_(True)))).scalar_one_or_none()
        if tenant is None:
            raise HTTPException(404, "tenant not found")
        st = await c.sessions.create(tenant_id=tenant.id, channel=Channel.VOICE, language=body.language or tenant.default_language)
        session_id, tenant_id = st.session_id, tenant.id
        token = c.jwt.session_token(session_id=session_id, tenant_id=tenant_id, ttl=c.settings.session_token_ttl_seconds)
    svc = c.voice
    state, room = await svc.start(session_id, tenant_id)
    return {"session": state.public_view(), "session_token": token, "room": room, "livekit_url": c.settings.livekit_public_url or c.settings.livekit_url,
            "participant_token": svc.customer_token(state, room)}


@router.post("/token")
async def voice_token(p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    """Fresh LiveKit participant token for reconnecting to the session's room."""
    state = await load_session(c, p.session_id, p.tenant_id)
    if not state.voice_room:
        raise HTTPException(409, "no voice session started; call POST /voice/session first")
    svc = c.voice
    return {"room": state.voice_room, "livekit_url": c.settings.livekit_public_url or c.settings.livekit_url, "participant_token": svc.customer_token(state, state.voice_room)}


class VoiceSimulate(BaseModel):
    transcript: str
    language: str | None = None


@router.post("/simulate")
async def simulate_voice_turn(body: VoiceSimulate, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    """Dev/test only: run a voice turn from a transcript (skips audio, STT and TTS vendors) through the real
    VoiceChannel + AgentRuntime, returning the text that would be sent to TTS."""
    if c.settings.is_production:
        raise HTTPException(404, "not found")
    from app.channels.voice.session import VoiceChannel

    await load_session(c, p.session_id, p.tenant_id)
    channel = VoiceChannel(c.runtime, c.sessions, p.session_id, p.tenant_id)
    resp = await channel.handle_utterance(body.transcript, body.language)
    return {"response": resp.model_dump(mode="json") if resp else None, "speech_text": channel.spoken[-1] if channel.spoken else None}
