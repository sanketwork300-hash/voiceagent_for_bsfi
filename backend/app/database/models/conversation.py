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
