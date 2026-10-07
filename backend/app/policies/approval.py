"""Binding confirmations / approvals to the exact action so the LLM cannot swap arguments afterwards."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.database.models import ApprovalRequest
from app.database.session import Database
from app.domain import new_id, utcnow


def action_hash(tool: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool, "args": arguments}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ActionGrant(BaseModel):
    """Produced only by the runtime from customer/staff input — never by the LLM."""

    action_hash: str
    confirmed: bool = False
    approval_id: str | None = None


class ApprovalService:
    def __init__(self, db: Database, ttl_seconds: int = 3600) -> None:
        self.db = db
        self.ttl = timedelta(seconds=ttl_seconds)

    async def request(self, *, tenant_id: str, session_id: str, conversation_id: str, tool: str,
                      arguments: dict[str, Any], risk_level: str, reason: str) -> ApprovalRequest:
        async with self.db.session() as s:
            req = ApprovalRequest(id=new_id(), tenant_id=tenant_id, session_id=session_id, conversation_id=conversation_id,
                                  tool_name=tool, arguments=arguments, args_hash=action_hash(tool, arguments),
                                  risk_level=risk_level, reason=reason)
            s.add(req)
        return req

    async def decide(self, *, tenant_id: str, approval_id: str, approve: bool, staff_user_id: str) -> ApprovalRequest | None:
        async with self.db.session() as s:
            req = (await s.execute(select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id))).scalar_one_or_none()
            if req is None or req.status != "pending":
                return req
            created = req.created_at if req.created_at.tzinfo else req.created_at.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - created > self.ttl:
                req.status = "expired"
                return req
            req.status = "approved" if approve else "rejected"
            req.decided_by = staff_user_id
            req.decided_at = utcnow()
            return req

    async def is_approved(self, tenant_id: str, approval_id: str, args_hash: str) -> bool:
        async with self.db.session() as s:
            req = (await s.execute(select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id))).scalar_one_or_none()
        return bool(req and req.status == "approved" and req.args_hash == args_hash)

    async def status(self, tenant_id: str, approval_id: str) -> str | None:
        async with self.db.session() as s:
            req = (await s.execute(select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.id == approval_id))).scalar_one_or_none()
        return req.status if req else None

    async def list_pending(self, tenant_id: str) -> list[ApprovalRequest]:
        async with self.db.session() as s:
            return list((await s.execute(select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == "pending"))).scalars())
