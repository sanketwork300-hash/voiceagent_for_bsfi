from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import container, ensure_session_access, load_session
from app.auth.authorization import (
    SessionPrincipal,
    StaffPrincipal,
    current_session,
    require,
)
from app.auth.permissions import Permission
from app.database.models import Conversation

router = APIRouter(tags=["conversations"])


def _msg(m) -> dict:
    return {"id": m.id, "role": m.role, "channel": m.channel, "content": m.content, "language": m.language, "intent": m.intent,
            "tool_calls": m.tool_calls, "sources": m.sources, "interrupted": m.interrupted, "created_at": m.created_at}


@router.get("/conversations")
async def list_conversations(limit: int = 50, p: StaffPrincipal = Depends(require(Permission.CONVERSATION_READ)), c=Depends(container)) -> list[dict]:
    async with c.db.session() as s:
        rows = (await s.execute(select(Conversation).where(Conversation.tenant_id == p.tenant_id)
                                .order_by(Conversation.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    return [{"id": r.id, "channel": r.channel, "channels_used": r.channels_used, "language": r.language, "status": r.status,
             "last_intent": r.last_intent, "customer_ref": r.customer_ref, "created_at": r.created_at} for r in rows]


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str, p: StaffPrincipal = Depends(require(Permission.CONVERSATION_READ)),
                                c=Depends(container)) -> list[dict]:
    msgs = await c.memory.all_messages(p.tenant_id, conversation_id)
    if not msgs:
        raise HTTPException(404, "conversation not found")
    return [_msg(m) for m in msgs]


@router.get("/sessions/{session_id}/messages")
async def my_messages(session_id: str, p: SessionPrincipal = Depends(current_session), c=Depends(container)) -> list[dict]:
    """Customer-visible transcript (works across chat and voice turns of the same conversation)."""
    ensure_session_access(p, session_id)
    state = await load_session(c, session_id, p.tenant_id)
    return [_msg(m) for m in await c.memory.all_messages(p.tenant_id, state.conversation_id) if m.role in ("user", "assistant", "human_agent")]
