"""Human agent desk: queue, accept, reply, resolve (optionally hand the customer back to the AI agent)."""

from __future__ import annotations

from sqlalchemy import select

from app.database.models import Handoff
from app.database.session import Database
from app.domain import SessionStatus
from app.escalation.handoff import session_events_channel
from app.security.audit import AuditLogger
from app.sessions.manager import SessionManager, StateStore
from app.sessions.memory import ConversationMemory


class HumanAgentDesk:
    def __init__(self, db: Database, sessions: SessionManager, memory: ConversationMemory, store: StateStore, audit: AuditLogger) -> None:
        self.db = db
        self.sessions = sessions
        self.memory = memory
        self.store = store
        self.audit = audit

    async def queue(self, tenant_id: str, status: str = "queued") -> list[Handoff]:
        order = {"urgent": 0, "high": 1, "normal": 2}
        async with self.db.session() as s:
            rows = list((await s.execute(select(Handoff).where(Handoff.tenant_id == tenant_id, Handoff.status == status)
                                         .order_by(Handoff.created_at))).scalars())
        return sorted(rows, key=lambda h: order.get(h.priority, 3))

    async def _get(self, tenant_id: str, handoff_id: str) -> Handoff:
        async with self.db.session() as s:
            h = (await s.execute(select(Handoff).where(Handoff.tenant_id == tenant_id, Handoff.id == handoff_id))).scalar_one_or_none()
        if h is None:
            raise LookupError("handoff not found")
        return h

    async def accept(self, tenant_id: str, handoff_id: str, staff_id: str) -> Handoff:
        async with self.db.session() as s:
            h = (await s.execute(select(Handoff).where(Handoff.tenant_id == tenant_id, Handoff.id == handoff_id))).scalar_one_or_none()
            if h is None:
                raise LookupError("handoff not found")
            h.status, h.assigned_to = "assigned", staff_id
        await self.store.publish(session_events_channel(h.session_id), {"type": "handoff.accepted", "agent": "human"})
        await self.audit.record(tenant_id, "handoff.accepted", actor_type="staff", actor_id=staff_id, resource=handoff_id,
                                session_id=h.session_id)
        return h

    async def reply(self, tenant_id: str, handoff_id: str, staff_id: str, text: str) -> None:
        h = await self._get(tenant_id, handoff_id)
        await self.memory.append(tenant_id=tenant_id, conversation_id=h.conversation_id, session_id=h.session_id,
                                 role="human_agent", channel=h.channel, content=text, extra={"staff_id": staff_id})
        await self.store.publish(session_events_channel(h.session_id), {"type": "human.message", "content": text})

    async def resolve(self, tenant_id: str, handoff_id: str, staff_id: str, resolution: str, return_to_agent: bool) -> Handoff:
        async with self.db.session() as s:
            h = (await s.execute(select(Handoff).where(Handoff.tenant_id == tenant_id, Handoff.id == handoff_id))).scalar_one_or_none()
            if h is None:
                raise LookupError("handoff not found")
            h.status, h.resolution = ("returned" if return_to_agent else "resolved"), resolution
        if return_to_agent:
            async with self.sessions.locked(h.session_id, tenant_id) as state:
                state.status = SessionStatus.ACTIVE
                state.handoff_id = None
                state.consecutive_failures = 0
        await self.store.publish(session_events_channel(h.session_id), {"type": "handoff.resolved", "returned_to_agent": return_to_agent})
        await self.audit.record(tenant_id, "handoff.resolved", actor_type="staff", actor_id=staff_id, resource=handoff_id,
                                session_id=h.session_id, payload={"returned_to_agent": return_to_agent})
        return h
