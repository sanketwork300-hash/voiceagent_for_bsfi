"""The single internal Tool contract that REST, OpenAPI, MCP, adapter and built-in tools normalise into."""

from __future__ import annotations

import copy
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain import AuthState, Channel, Intent, RiskLevel
from app.llm.base import LLMToolSpec


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    REST = "rest"
    OPENAPI = "openapi"
    MCP = "mcp"
    ADAPTER = "adapter"


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    risk_level: RiskLevel = RiskLevel.MEDIUM
    min_auth_state: AuthState = AuthState.FULLY_AUTHENTICATED
    requires_confirmation: bool = False
    source: ToolSource
    integration_id: str | None = None
    binding: dict[str, Any] = Field(default_factory=dict)  # transport specifics (method/path, mcp server/remote name ...)
    # Parameters the platform fills from trusted session context; hidden from the LLM and overwritten if supplied.
    injected_params: dict[str, str] = Field(default_factory=dict)
    intents: list[Intent] = Field(default_factory=list)  # empty => offered for any intent
    confirmation_template: str | None = None
    timeout_seconds: float = 10.0
    idempotent: bool = False
    internal: bool = False  # platform-only (e.g. OTP verification); never offered to the LLM
    enabled: bool = True

    def llm_schema(self) -> dict[str, Any]:
        schema = copy.deepcopy(self.input_schema) or {"type": "object", "properties": {}}
        props = schema.setdefault("properties", {})
        for p in self.injected_params:
            props.pop(p, None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in self.injected_params]
        schema.setdefault("additionalProperties", False)
        return schema

    def llm_spec(self) -> LLMToolSpec:
        return LLMToolSpec(
            name=self.name,
            description=f"{self.description} [risk_level: {self.risk_level.value}]",
            parameters=self.llm_schema(),
        )

    def llm_view(self) -> dict[str, Any]:
        """What the model 'sees' — identical regardless of REST/OpenAPI/MCP backing."""
        return {"name": self.name, "description": self.description, "input_schema": self.llm_schema(),
                "risk_level": self.risk_level.value}


class ToolContext(BaseModel):
    """Trusted execution context built by the runtime from the session store — never from LLM output."""

    tenant_id: str
    session_id: str
    conversation_id: str
    agent_id: str | None = None
    customer_id: str | None = None
    channel: Channel
    language: str = "en"
    auth_state: AuthState = AuthState.UNAUTHENTICATED
    auth_methods: list[str] = Field(default_factory=list)
    txn_auth_action_hash: str | None = None  # TRANSACTION_AUTHENTICATED is bound to exactly one action
    intent: Intent | None = None
    request_id: str | None = None
    session_stats: dict[str, Any] = Field(default_factory=dict)

    def value_for(self, key: str) -> Any:
        return {"customer_id": self.customer_id, "session_id": self.session_id, "tenant_id": self.tenant_id,
                "channel": self.channel.value, "conversation_id": self.conversation_id,
                "request_id": self.request_id}.get(key)


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str | None = None
    policy_decision: str | None = None
    latency_ms: float | None = None

    def to_llm_content(self) -> str:
        body: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            body["data"] = self.data
        else:
            body["error"] = self.error
            if self.policy_decision:
                body["policy_decision"] = self.policy_decision
        return json.dumps(body, default=str, ensure_ascii=False)


class ToolExecutionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
