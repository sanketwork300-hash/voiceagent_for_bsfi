"""Tamper-evident audit trail.

Each event stores `hash = sha256(prev_hash || canonical(event))`, chained per tenant, so deleting or
editing a row breaks verification. Payloads are redacted with the AUDIT profile before hashing.
Appends are serialised per tenant (in-process lock + PostgreSQL advisory lock across replicas), and a
unique (tenant_id, seq) constraint guarantees the chain can never fork.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select, text

from app.database.models import AuditEvent
from app.database.session import Database
from app.domain import new_id, utcnow
from app.observability.tracing import current_trace_id
from app.security.redaction import RedactionProfile, redact_data

log = logging.getLogger(__name__)
GENESIS = "0" * 64


def _canonical(ev: dict[str, Any]) -> str:
    return json.dumps(ev, sort_keys=True, default=str, separators=(",", ":"))


def compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(body)).encode()).hexdigest()


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def record(
        self,
        tenant_id: str,
        event_type: str,
        *,
        actor_type: str = "agent_runtime",
        actor_id: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        channel: str | None = None,
        resource: str | None = None,
        outcome: str = "success",
        payload: dict[str, Any] | None = None,
    ) -> None:
        body = {
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "channel": channel,
            "resource": resource,
            "outcome": outcome,
            "payload": redact_data(payload or {}, RedactionProfile.AUDIT),
        }
        try:
            async with self._locks[tenant_id], self.db.session() as s:
                if self.db.engine.dialect.name == "postgresql":
                    # serialise chain appends per tenant across ALL backend replicas, released at commit
                    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit:{tenant_id}"})
                last = (
                    await s.execute(
                        select(AuditEvent.seq, AuditEvent.hash)
                        .where(AuditEvent.tenant_id == tenant_id)
                        .order_by(AuditEvent.seq.desc())
                        .limit(1)
                    )
                ).first()
                seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
                occurred = utcnow()
                chained = {**body, "seq": seq, "occurred_at": occurred.isoformat(), "tenant_id": tenant_id}
                s.add(
                    AuditEvent(
                        id=new_id(), tenant_id=tenant_id, seq=seq, occurred_at=occurred,
                        trace_id=current_trace_id(), prev_hash=prev, hash=compute_hash(prev, chained), **body,
                    )
                )
        except Exception:  # audit must never take the conversation down, but it must be loud
            log.exception("audit write failed", extra={"event_type": event_type})

    async def verify_chain(self, tenant_id: str) -> tuple[bool, int | None]:
        """Returns (ok, first_broken_seq)."""
        async with self.db.session() as s:
            rows = (
                await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq))
            ).scalars().all()
        prev = GENESIS
        for r in rows:
            body = {
                "event_type": r.event_type, "actor_type": r.actor_type, "actor_id": r.actor_id,
                "session_id": r.session_id, "conversation_id": r.conversation_id, "channel": r.channel,
                "resource": r.resource, "outcome": r.outcome, "payload": r.payload, "seq": r.seq,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at.tzinfo else r.occurred_at.isoformat() + "+00:00",
                "tenant_id": tenant_id,
            }
            if r.prev_hash != prev or compute_hash(prev, body) != r.hash:
                return False, r.seq
            prev = r.hash
        return True, None
