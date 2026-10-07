"""Shared conversation/session state. One model for chat and voice, so a customer can switch channels
mid-conversation (chat -> voice -> chat) and keep intent, language, auth, pending action and context."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.domain import (
    AuthState,
    Channel,
    Intent,
    PendingActionView,
    PolicyDecisionType,
    SessionStatus,
    SourceCitation,
    new_id,
    utcnow,
)


class AuthChallenge(BaseModel):
    """An outstanding OTP challenge. The OTP value itself is never stored — only the bank's challenge id."""

    challenge_id: str
    purpose: str  # login | transaction
    action_hash: str | None = None
    masked_destination: str | None = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class PendingAction(BaseModel):
    """A tool call frozen by the policy engine until the customer authenticates / confirms / is approved.

    The arguments are frozen at creation: confirmation executes exactly these, never a re-generated call.
    """

    id: str = Field(default_factory=new_id)
    tool: str
    arguments: dict[str, Any]
    action_hash: str
    decision: PolicyDecisionType
    reason: str
    risk_level: str
    summary: str
    required_auth_state: AuthState | None = None
    approval_id: str | None = None
    confirmed: bool = False
    intent: Intent | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime

    @classmethod
    def create(cls, ttl_seconds: int, **kw: Any) -> PendingAction:
        return cls(expires_at=utcnow() + timedelta(seconds=ttl_seconds), **kw)

    @property
    def expired(self) -> bool:
        return utcnow() > self.expires_at

    def view(self) -> PendingActionView:
        return PendingActionView(id=self.id, tool=self.tool, decision=self.decision, summary=self.summary,
                                 required_auth_state=self.required_auth_state, expires_at=self.expires_at)


class ToolResultMemo(BaseModel):
    tool: str
    ok: bool
    summary: dict[str, Any]  # redacted
    at: datetime = Field(default_factory=utcnow)


class SessionState(BaseModel):
    session_id: str
    tenant_id: str
    conversation_id: str
    agent_id: str | None = None
    customer_id: str | None = None  # bank's opaque customer reference
    channel: Channel
    active_channels: list[Channel] = Field(default_factory=list)
    language: str = "en"
    language_script: str = "Latn"
    authentication_state: AuthState = AuthState.UNAUTHENTICATED
    auth_methods: list[str] = Field(default_factory=list)
    txn_auth_action_hash: str | None = None
    auth_challenge: AuthChallenge | None = None
    auth_failures: int = 0
    current_intent: Intent | None = None
    pending_action: PendingAction | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    handoff_id: str | None = None
    consecutive_failures: int = 0
    policy_rejections: int = 0
    fraud_flagged: bool = False
    transfer_total_today: float = 0.0
    transfer_count_today: int = 0
    last_tool_results: list[ToolResultMemo] = Field(default_factory=list)
    rag_context: list[SourceCitation] = Field(default_factory=list)
    voice_room: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime = Field(default_factory=utcnow)

    @property
    def response_language_tag(self) -> str:
        return f"{self.language}-Latn" if self.language != "en" and self.language_script == "Latn" else self.language

    def session_stats(self) -> dict[str, Any]:
        return {"transfer_total_today": self.transfer_total_today, "transfer_count_today": self.transfer_count_today,
                "auth_failures": self.auth_failures, "fraud_flagged": self.fraud_flagged,
                "policy_rejections": self.policy_rejections}

    def remember_tool(self, tool: str, ok: bool, summary: dict[str, Any], keep: int = 5) -> None:
        self.last_tool_results = [*self.last_tool_results[-(keep - 1):], ToolResultMemo(tool=tool, ok=ok, summary=summary)]

    def public_view(self) -> dict[str, Any]:
        """Client-safe view (spec §23)."""
        return {
            "session_id": self.session_id, "conversation_id": self.conversation_id, "channel": self.channel.value,
            "active_channels": [c.value for c in self.active_channels], "language": self.language,
            "authentication_state": self.authentication_state.value,
            "current_intent": self.current_intent.value if self.current_intent else None,
            "pending_action": self.pending_action.view().model_dump(mode="json") if self.pending_action else None,
            "status": self.status.value, "handoff_id": self.handoff_id,
        }
