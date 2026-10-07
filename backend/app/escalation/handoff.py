"""Human handoff — one mechanism for chat and voice.

Chat: the session is flagged HANDED_OFF; customer messages are relayed to the human desk and human replies
are pushed to the customer's channel. Voice: additionally a `transfer` command is published for the LiveKit
worker that owns the call, which performs a SIP transfer (or brings a human into the WebRTC room).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.agents.state import SessionState
from app.database.models import Handoff, ToolExecution
from app.database.session import Database
from app.domain import Channel, HandoffReason, SessionStatus, new_id
from app.llm.base import LLMMessage, LLMProvider
from app.observability import metrics
from app.security.audit import AuditLogger
from app.security.redaction import RedactionProfile, redact, redact_data
from app.sessions.manager import StateStore
from app.sessions.memory import ConversationMemory

log = logging.getLogger(__name__)

PRIORITY = {
    HandoffReason.FRAUD: "urgent",
    HandoffReason.AUTHENTICATION_FAILURE: "high",
    HandoffReason.HIGH_RISK_OPERATION: "high",
    HandoffReason.POLICY_REJECTION: "normal",
    HandoffReason.REPEATED_FAILURE: "normal",
    HandoffReason.CUSTOMER_REQUEST: "normal",
    HandoffReason.COMPLEX_COMPLAINT: "high",
}


def desk_channel(tenant_id: str) -> str:
    return f"desk:{tenant_id}"


def session_events_channel(session_id: str) -> str:
    return f"session-events:{session_id}"


def voice_control_channel(session_id: str) -> str:
    return f"voice-control:{session_id}"


class HandoffService:
    def __init__(self, db: Database, memory: ConversationMemory, llm: LLMProvider, store: StateStore, audit: AuditLogger) -> None:
        self.db = db
        self.memory = memory
        self.llm = llm
        self.store = store
        self.audit = audit

    async def summarize(self, state: SessionState) -> str:
        msgs = await self.memory.history(state.tenant_id, state.conversation_id, limit=30)
        transcript = "\n".join(f"{'customer' if m.role == 'user' else m.role}: {m.content}" for m in msgs)
        try:
            resp = await self.llm.complete([
                LLMMessage(role="system", content="TASK: SUMMARIZE. Write a 2-3 sentence factual handoff note for a "
                           "bank contact-centre agent: what the customer wants, what was done, what is pending. No speculation."),
                LLMMessage(role="user", content=transcript[-6000:]),
            ], temperature=0)
            return redact(resp.content.strip(), RedactionProfile.AUDIT)
        except Exception:  # summary is best-effort; handoff must not fail because of it
            log.warning("handoff summary failed")
            return redact(transcript[-500:], RedactionProfile.AUDIT)

    async def initiate(self, state: SessionState, reason: HandoffReason, note: str | None = None) -> Handoff:
        """Caller must hold the session lock and save `state` afterwards."""
        if state.status == SessionStatus.HANDED_OFF and state.handoff_id:
            async with self.db.session() as s:
                existing = await s.get(Handoff, state.handoff_id)
            if existing:
                return existing
        async with self.db.session() as s:
            execs = (await s.execute(select(ToolExecution).where(
                ToolExecution.tenant_id == state.tenant_id, ToolExecution.conversation_id == state.conversation_id)
                .order_by(ToolExecution.created_at))).scalars().all()
        tools_called = [{"tool": e.tool_name, "status": e.status, "decision": e.policy_decision} for e in execs]
        actions_taken = [{"tool": e.tool_name, "result": e.result} for e in execs if e.status == "completed" and e.risk_level in ("HIGH", "CRITICAL")]
        context: dict[str, Any] = {
            "conversation_id": state.conversation_id,
            "customer_id": state.customer_id,
            "intent": state.current_intent.value if state.current_intent else None,
            "authentication_status": state.authentication_state.value,
            "summary": await self.summarize(state),
            "tools_called": tools_called,
            "actions_taken": redact_data(actions_taken, RedactionProfile.AUDIT),
            "reason": reason.value,
            "note": redact(note, RedactionProfile.AUDIT) if note else None,
            "channel": state.channel.value,
            "language": state.language,
            "pending_action": state.pending_action.view().model_dump(mode="json") if state.pending_action else None,
        }
        handoff = Handoff(id=new_id(), tenant_id=state.tenant_id, conversation_id=state.conversation_id,
                          session_id=state.session_id, customer_ref=state.customer_id, channel=state.channel.value,
                          reason=reason.value, priority=PRIORITY[reason], context=context)
        async with self.db.session() as s:
            s.add(handoff)
        state.status = SessionStatus.HANDED_OFF
        state.handoff_id = handoff.id
        metrics.handoffs.labels(state.channel.value, reason.value).inc()
        await self.audit.record(state.tenant_id, "handoff.initiated", session_id=state.session_id,
                                conversation_id=state.conversation_id, channel=state.channel.value,
                                resource=handoff.id, payload={"reason": reason.value, "priority": handoff.priority})
        await self.store.publish(desk_channel(state.tenant_id), {"type": "handoff.queued", "handoff_id": handoff.id,
                                                                  "priority": handoff.priority, "context": context})
        if state.channel == Channel.VOICE:
            await self.store.publish(voice_control_channel(state.session_id), {"type": "transfer", "handoff_id": handoff.id,
                                                                                "priority": handoff.priority})
        return handoff

    async def relay_customer_message(self, state: SessionState, text: str) -> None:
        await self.store.publish(desk_channel(state.tenant_id), {"type": "customer.message", "handoff_id": state.handoff_id,
                                                                  "session_id": state.session_id, "text": text})
