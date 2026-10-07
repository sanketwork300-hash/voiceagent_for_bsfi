from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events")
async def events(event_type: str | None = None, session_id: str | None = None, limit: int = 100,
                 p: StaffPrincipal = Depends(require(Permission.AUDIT_READ)), c=Depends(container)) -> list[dict]:
    q = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    if session_id:
        q = q.where(AuditEvent.session_id == session_id)
    async with c.db.session() as s:
        rows = (await s.execute(q.order_by(AuditEvent.seq.desc()).limit(min(limit, 1000)))).scalars().all()
    return [{"seq": r.seq, "occurred_at": r.occurred_at, "event_type": r.event_type, "actor_type": r.actor_type, "actor_id": r.actor_id,
             "session_id": r.session_id, "channel": r.channel, "resource": r.resource, "outcome": r.outcome, "payload": r.payload,
             "trace_id": r.trace_id, "hash": r.hash} for r in rows]


@router.get("/verify")
async def verify(p: StaffPrincipal = Depends(require(Permission.AUDIT_READ)), c=Depends(container)) -> dict:
    ok, broken = await c.audit.verify_chain(p.tenant_id)
    return {"intact": ok, "first_broken_seq": broken}
