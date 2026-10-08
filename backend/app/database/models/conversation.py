from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class Conversation(IdMixin, TimestampMixin, TenantScoped, Base):
    """One logical conversation. It survives channel switches (chat <-> voice)."""

    __tablename__ = "conversations"

    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(16))  # channel the conversation started on
    channels_used: Mapped[list] = mapped_column(default=list)
    language: Mapped[str] = mapped_column(String(16), default="en")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    last_intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Session(IdMixin, TimestampMixin, TenantScoped, Base):
    """Durable mirror of the hot session state kept in Redis."""

    __tablename__ = "sessions"

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(16), default="en")
    authentication_state: Mapped[str] = mapped_column(String(32), default="UNAUTHENTICATED")
    current_intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    state: Mapped[dict] = mapped_column(default=dict)  # snapshot of SessionState (secrets never included)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationMessage(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant|tool|system|human_agent
    channel: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)  # authentication secrets are stripped before persistence
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_calls: Mapped[list] = mapped_column(default=list)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sources: Mapped[list] = mapped_column(default=list)
    interrupted: Mapped[bool] = mapped_column(default=False)
    extra: Mapped[dict] = mapped_column(default=dict)


class ToolExecution(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tool_executions"

    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    arguments: Mapped[dict] = mapped_column(default=dict)  # redacted
    result: Mapped[dict] = mapped_column(default=dict)  # redacted
    status: Mapped[str] = mapped_column(String(16))  # completed|failed|denied|pending
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AgentWorkflow(IdMixin, TimestampMixin, TenantScoped, Base):
    """Durable workflow state (app.agents.execution.workflow). Survives worker restarts and Redis eviction."""

    __tablename__ = "agent_workflows"

    session_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(default=dict)  # redacted-safe serialised WorkflowState
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VoiceCall(IdMixin, TimestampMixin, TenantScoped, Base):
    """One phone / WebRTC voice call bound to an agent session. Holds no raw phone number: only a masked display
    value and a keyed caller reference (app.channels.voice.telephony.caller_ref)."""

    __tablename__ = "voice_calls"

    session_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    transport: Mapped[str] = mapped_column(String(16))  # sip | webrtc
    direction: Mapped[str] = mapped_column(String(16))  # inbound | outbound
    room_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # digits masked
    participant_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)  # digits masked
    sip_call_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    sip_trunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sip_rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dialed_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the institution's own number
    caller_number_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caller_ref: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # active|completed|transferred|rejected|failed
    end_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
