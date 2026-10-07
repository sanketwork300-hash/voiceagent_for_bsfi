from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class Policy(IdMixin, TimestampMixin, TenantScoped, Base):
    """A tenant-configurable policy rule evaluated by the PolicyEngine (see app/policies/rules.py)."""

    __tablename__ = "policies"

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    conditions: Mapped[dict] = mapped_column(default=dict)
    effect: Mapped[str] = mapped_column(String(32))  # PolicyDecisionType
    params: Mapped[dict] = mapped_column(default=dict)  # e.g. {"required_auth_state": "..."}
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditEvent(IdMixin, TenantScoped, Base):
    """Append-only, hash-chained audit trail. Payloads are PII-redacted before they reach here."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("tenant_id", "seq", name="uq_audit_tenant_seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(32))  # customer|agent_runtime|staff|system
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="success")
    payload: Mapped[dict] = mapped_column(default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Handoff(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "handoffs"

    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # normal|high|urgent
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|assigned|resolved|returned
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context: Mapped[dict] = mapped_column(default=dict)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalRequest(IdMixin, TimestampMixin, TenantScoped, Base):
    """Maker-checker approval for actions the policy engine routed to REQUIRE_HUMAN_APPROVAL."""

    __tablename__ = "approval_requests"

    session_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str] = mapped_column(String(36))
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict] = mapped_column(default=dict)
    args_hash: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected|expired
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
