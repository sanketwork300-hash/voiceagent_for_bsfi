from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, status
from pydantic import BaseModel, Field

from app.api.deps import container, ensure_session_access, load_session
from app.auth.authorization import SessionPrincipal, current_session
from app.auth.jwt import TokenError
from app.channels.chat.gateway import ChatGateway
from app.channels.chat.websocket import WebSocketChatChannel
from app.domain import AgentResponse

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=4000)
    language: str | None = None


@router.post("/chat/message", response_model=AgentResponse)
async def chat_message(body: ChatMessage, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> AgentResponse:
    ensure_session_access(p, body.session_id)
    await load_session(c, body.session_id, p.tenant_id)
    return await ChatGateway(c.runtime, c.sessions).handle_message(
        session_id=body.session_id, tenant_id=p.tenant_id, text=body.message, language_hint=body.language)


@router.websocket("/ws/chat/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str, token: str = Query(...)) -> None:
    c = ws.app.state.container

    async def reject() -> None:
        # Accept-then-close so browsers receive the 1008 code (a pre-handshake rejection surfaces only as 1006),
        # letting clients stop retrying. Nothing is sent before the close.
        await ws.accept()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    try:
        claims = c.jwt.verify(token, "session")
    except TokenError:
        return await reject()
    if claims["sub"] != session_id:
        return await reject()
    try:
        await c.sessions.get(session_id, claims["tenant_id"])
    except LookupError:
        return await reject()
    await ws.accept()
    await WebSocketChatChannel(ws, c, session_id, claims["tenant_id"]).serve()
