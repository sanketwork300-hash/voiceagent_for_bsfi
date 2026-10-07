from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, status
from pydantic import BaseModel

from app.api.deps import container, ensure_session_access
from app.auth.authorization import (
    SessionPrincipal,
    StaffPrincipal,
    current_session,
    require,
)
from app.auth.jwt import TokenError
from app.auth.permissions import Permission, permissions_for
from app.domain import HandoffReason
from app.escalation.handoff import desk_channel

router = APIRouter(tags=["handoff"])


class HandoffRequest(BaseModel):
    session_id: str
    reason: HandoffReason = HandoffReason.CUSTOMER_REQUEST
    note: str | None = None


@router.post("/handoff", status_code=201)
async def request_handoff(body: HandoffRequest, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> dict:
    """Customer-initiated handoff (e.g. a 'Talk to an agent' button). Same mechanism the runtime uses."""
    ensure_session_access(p, body.session_id)
    async with c.sessions.locked(body.session_id, p.tenant_id) as st:
        h = await c.handoff.initiate(st, body.reason, body.note)
    return {"handoff_id": h.id, "status": h.status, "priority": h.priority}


@router.get("/handoff/queue")
async def queue(status_: str = Query("queued", alias="status"), p: StaffPrincipal = Depends(require(Permission.HANDOFF_HANDLE)),
                c=Depends(container)) -> list[dict]:
    return [{"id": h.id, "session_id": h.session_id, "conversation_id": h.conversation_id, "channel": h.channel, "reason": h.reason,
             "priority": h.priority, "status": h.status, "context": h.context, "created_at": h.created_at}
            for h in await c.desk.queue(p.tenant_id, status_)]


@router.post("/handoff/{handoff_id}/accept")
async def accept(handoff_id: str, p: StaffPrincipal = Depends(require(Permission.HANDOFF_HANDLE)), c=Depends(container)) -> dict:
    h = await c.desk.accept(p.tenant_id, handoff_id, p.user_id)
    return {"id": h.id, "status": h.status, "context": h.context}


class Reply(BaseModel):
    text: str


@router.post("/handoff/{handoff_id}/reply")
async def reply(handoff_id: str, body: Reply, p: StaffPrincipal = Depends(require(Permission.HANDOFF_HANDLE)), c=Depends(container)) -> dict:
    await c.desk.reply(p.tenant_id, handoff_id, p.user_id, body.text)
    return {"sent": True}


class Resolve(BaseModel):
    resolution: str
    return_to_agent: bool = False


@router.post("/handoff/{handoff_id}/resolve")
async def resolve(handoff_id: str, body: Resolve, p: StaffPrincipal = Depends(require(Permission.HANDOFF_HANDLE)), c=Depends(container)) -> dict:
    h = await c.desk.resolve(p.tenant_id, handoff_id, p.user_id, body.resolution, body.return_to_agent)
    return {"id": h.id, "status": h.status}


@router.websocket("/ws/desk")
async def desk_ws(ws: WebSocket, token: str = Query(...)) -> None:
    """Live feed for the human-agent interface: queued handoffs (with context) and customer messages."""
    c = ws.app.state.container
    try:
        claims = c.jwt.verify(token, "access")
    except TokenError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if Permission.HANDOFF_HANDLE not in permissions_for(claims.get("roles", [])):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    try:
        async for ev in c.store.subscribe(desk_channel(claims["tenant_id"])):
            await ws.send_json(ev)
    except Exception:  # client went away
        return


