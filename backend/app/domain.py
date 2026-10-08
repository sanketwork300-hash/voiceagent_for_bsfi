"""Shared domain vocabulary and the channel-agnostic request/response contracts.

Everything in here is imported by channels, the runtime, tools and policies alike, so it must not
import any other application module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Channel(StrEnum):
    CHAT = "chat"
    VOICE = "voice"


class AuthState(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    IDENTIFIED = "IDENTIFIED"
    PARTIALLY_AUTHENTICATED = "PARTIALLY_AUTHENTICATED"
    FULLY_AUTHENTICATED = "FULLY_AUTHENTICATED"
    TRANSACTION_AUTHENTICATED = "TRANSACTION_AUTHENTICATED"

    @property
    def level(self) -> int:
        return _AUTH_ORDER.index(self)

    def satisfies(self, required: AuthState) -> bool:
        return self.level >= required.level


_AUTH_ORDER = list(AuthState)


class AuthMethod(StrEnum):
    """How an authentication level was reached. Policies may require specific factors."""

    CUSTOMER_ASSERTION = "customer_assertion"  # signed by the bank's own IdP (app / netbanking login)
    CALLER_ID = "caller_id"
    KNOWLEDGE = "knowledge"  # e.g. DOB + last 4 digits
    VOICE_BIOMETRIC = "voice_biometric"
    OTP = "otp"
    TRANSACTION_OTP = "transaction_otp"


class Intent(StrEnum):
    KNOWLEDGE_QUERY = "KNOWLEDGE_QUERY"
    CUSTOMER_DATA_QUERY = "CUSTOMER_DATA_QUERY"
    ACTION_REQUEST = "ACTION_REQUEST"
    FRAUD_REQUEST = "FRAUD_REQUEST"
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def level(self) -> int:
        return list(RiskLevel).index(self)


class PolicyDecisionType(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_AUTH = "REQUIRE_AUTH"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"


class HandoffReason(StrEnum):
    FRAUD = "FRAUD"
    HIGH_RISK_OPERATION = "HIGH_RISK_OPERATION"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    POLICY_REJECTION = "POLICY_REJECTION"
    COMPLEX_COMPLAINT = "COMPLEX_COMPLAINT"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    HANDED_OFF = "HANDED_OFF"
    CLOSED = "CLOSED"


class OutputModality(StrEnum):
    """Rendering hint only. The runtime's business logic never branches on it."""

    TEXT = "text"
    SPEECH = "speech"


# ---------------------------------------------------------------------------------------------
# Normalized runtime contracts
# ---------------------------------------------------------------------------------------------


class AgentRequest(BaseModel):
    """What every channel hands to the AgentRuntime."""

    tenant_id: str
    session_id: str
    user_id: str | None = None
    channel: Channel
    language: str | None = None  # hint (e.g. STT-detected); runtime re-detects from text
    message: str
    authentication_state: AuthState = AuthState.UNAUTHENTICATED  # informational; session store is authoritative
    output_modality: OutputModality = OutputModality.TEXT
    request_id: str = Field(default_factory=new_id)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallSummary(BaseModel):
    id: str
    name: str
    status: Literal["completed", "failed", "denied", "pending"]
    decision: PolicyDecisionType | None = None
    latency_ms: float | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)  # redacted


class SourceCitation(BaseModel):
    index: int
    document_id: str
    title: str
    version: str | None = None
    page: int | None = None
    section: str | None = None
    chunk_id: str
    score: float
    snippet: str


class PendingActionView(BaseModel):
    id: str
    tool: str
    decision: PolicyDecisionType
    summary: str
    required_auth_state: AuthState | None = None
    expires_at: datetime


class AgentResponse(BaseModel):
    """What the AgentRuntime hands back to every channel."""

    text: str
    intent: Intent | None = None
    language: str = "en"
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    sources: list[SourceCitation] = Field(default_factory=list)
    handoff: bool = False
    handoff_id: str | None = None
    pending_action: PendingActionView | None = None
    authentication_state: AuthState = AuthState.UNAUTHENTICATED
    session_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    interrupted: bool = False
    error: str | None = None


RuntimeEventType = Literal[
    "processing.started",
    "intent.detected",
    "message.delta",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "knowledge.sources",
    "auth.required",
    "confirmation.required",
    "approval.required",
    "handoff.initiated",
    "workflow.progress",
    "verification.completed",
    "message.completed",
    "error",
]


class RuntimeEvent(BaseModel):
    type: RuntimeEventType
    content: str | None = None
    tool: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    response: AgentResponse | None = None  # only on message.completed
