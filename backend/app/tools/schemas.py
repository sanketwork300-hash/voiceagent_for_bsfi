"""The single internal Tool contract that REST, OpenAPI, MCP, adapter and built-in tools normalise into."""

from __future__ import annotations

import copy
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain import AuthState, Channel, Intent, RiskLevel
from app.llm.base import LLMToolSpec


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    REST = "rest"
    OPENAPI = "openapi"
    MCP = "mcp"
    ADAPTER = "adapter"


class OperationType(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    VERIFY = "VERIFY"


class SideEffect(StrEnum):
    NONE = "NONE"
    ACCOUNT_READ = "ACCOUNT_READ"
    ACCOUNT_MUTATION = "ACCOUNT_MUTATION"
    FINANCIAL_MUTATION = "FINANCIAL_MUTATION"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"


MUTATING_SIDE_EFFECTS = frozenset({SideEffect.ACCOUNT_MUTATION, SideEffect.FINANCIAL_MUTATION, SideEffect.EXTERNAL_SIDE_EFFECT})


class ExecutionMetadata(BaseModel):
    """How a tool may be scheduled. Always server-side configuration — never taken from LLM output.

    Integrations may *declare* values (OpenAPI `x-bfsi-*`, MCP `_meta.bfsi`), but `resolve_execution` merges them
    conservatively: a mutation can never be READ, and mutations are never parallel-safe.
    """

    operation_type: OperationType = OperationType.WRITE
    side_effect: SideEffect = SideEffect.ACCOUNT_MUTATION
    parallel_safe: bool = False
    idempotent: bool = False
    requires_human_approval: bool = False  # static hint; dynamic maker-checker comes from policy rules
    depends_on: list[str] = Field(default_factory=list)  # tools that must complete first when both are in a plan
    concurrency_group: str | None = None  # e.g. "banking_api"; defaults to the integration/server
    max_concurrency: int | None = None  # per-tool cap (in-process)

    @property
    def mutates(self) -> bool:
        return self.operation_type == OperationType.WRITE or self.side_effect in MUTATING_SIDE_EFFECTS


def resolve_execution(*, source: ToolSource, binding: dict[str, Any], idempotent: bool, internal: bool,
                      declared: dict[str, Any] | None = None, read_hint: bool | None = None,
                      default_group: str | None = None) -> ExecutionMetadata:
    """Derive trusted scheduling metadata. Conservative by default: unknown => mutating, serialized."""
    method = str(binding.get("method", "")).upper()
    looks_read = (method == "GET") or (read_hint is True) or source == ToolSource.BUILTIN
    declared = dict(declared or {})
    op = OperationType(declared.get("operation_type") or (OperationType.READ if looks_read else OperationType.WRITE))
    effect = SideEffect(declared.get("side_effect") or (
        SideEffect.NONE if source == ToolSource.BUILTIN else SideEffect.ACCOUNT_READ if op != OperationType.WRITE else SideEffect.ACCOUNT_MUTATION))
    # A write transport (POST/destructive MCP) can't be declared a pure read, and a mutating effect can't be READ/VERIFY.
    if (not looks_read and op != OperationType.WRITE) or effect in MUTATING_SIDE_EFFECTS:
        op = OperationType.WRITE
        if effect not in MUTATING_SIDE_EFFECTS:
            effect = SideEffect.ACCOUNT_MUTATION
    mutates = op == OperationType.WRITE
    return ExecutionMetadata(
        operation_type=op, side_effect=effect,
        parallel_safe=False if mutates else bool(declared.get("parallel_safe", True)),  # mutations: never parallel
        idempotent=bool(declared.get("idempotent", idempotent or not mutates)),
        requires_human_approval=bool(declared.get("requires_human_approval", False)),
        depends_on=[str(x) for x in declared.get("depends_on", [])],
        concurrency_group=declared.get("concurrency_group") or default_group,
        max_concurrency=declared.get("max_concurrency"),
    )


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
    execution: ExecutionMetadata | None = None  # filled by resolve_execution when not given
    # Parameters filled by a server-side workflow from earlier steps (e.g. beneficiary_id); hidden from the LLM.
    bound_params: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.execution is None:
            self.execution = resolve_execution(source=self.source, binding=self.binding, idempotent=self.idempotent,
                                               internal=self.internal, default_group=self.integration_id)

    @property
    def exec(self) -> ExecutionMetadata:
        assert self.execution is not None
        return self.execution

    @property
    def requires_auth(self) -> bool:
        return self.min_auth_state != AuthState.UNAUTHENTICATED

    def llm_schema(self) -> dict[str, Any]:
        schema = copy.deepcopy(self.input_schema) or {"type": "object", "properties": {}}
        props = schema.setdefault("properties", {})
        hidden = set(self.injected_params) | set(self.bound_params)
        for p in hidden:
            props.pop(p, None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in hidden]
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
    # Set by the execution engine for one step: propagated to the institution (Idempotency-Key / MCP _meta)
    workflow_id: str | None = None
    step_id: str | None = None
    idempotency_key: str | None = None

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
    # What is known about the institution-side effect of a failed call:
    #   not_executed - never sent (unknown tool, invalid args, policy hold/deny)
    #   rejected     - the institution explicitly declined it (4xx / tool error): nothing was processed
    #   unknown      - sent but not confirmed (timeout, 5xx, transport error): outcome must be checked
    failure_kind: Literal["not_executed", "rejected", "unknown"] | None = None
    failure_category: str | None = None  # app.agents.execution.failures.FailureCategory
    status_code: int | None = None
    attempts: int = 1

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
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None,
                 category: str | None = None, sent: bool | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.category = category  # app.tools.failures.FailureCategory value when the raiser knows it
        self.sent = sent  # False: provably never reached the institution (e.g. connection refused)
        self.retry_after = retry_after
