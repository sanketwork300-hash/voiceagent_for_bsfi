"""Structured types of the execution engine: reasoning summary, plan (DAG of steps), step/verification results,
workflow state and the per-turn ExecutionResult. Nothing here is free-form model text: the LLM *proposes* tool calls,
these types describe what the platform decided to do with them."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from app.domain import Intent, RiskLevel, new_id, utcnow
from app.tools.schemas import OperationType, SideEffect


class StepKind(StrEnum):
    TOOL = "tool"  # a tool call through the Tool Gateway (policy-gated)
    CHECK = "check"  # deterministic server-side validation of earlier outputs (no I/O)
    VERIFY = "verify"  # read-only check of a mutation's real outcome in the system of record


class StepOrigin(StrEnum):
    LLM = "llm"  # proposed by the model (untrusted request)
    TEMPLATE = "template"  # added by a trusted server-side workflow template


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUBMITTING = "SUBMITTING"  # write-ahead: a mutation is (about to be) on the wire; recovery must verify, never resend
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # mutation sent, outcome not confirmed — requires verification
    HELD = "HELD"  # waiting for authentication / confirmation / approval
    SKIPPED = "SKIPPED"  # a dependency did not complete
    REJECTED = "REJECTED"  # removed by plan policy (e.g. a second money movement in one request)
    CANCELLED = "CANCELLED"  # workflow timed out / cancelled before the step started


TERMINAL_STEP = frozenset({StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.REJECTED,
                           StepStatus.CANCELLED})


class WorkflowStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    WAITING_FOR_AUTH = "WAITING_FOR_AUTH"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


ACTIVE_WORKFLOW = frozenset({WorkflowStatus.CREATED, WorkflowStatus.PLANNING, WorkflowStatus.WAITING_FOR_AUTH,
                             WorkflowStatus.WAITING_FOR_CONFIRMATION, WorkflowStatus.WAITING_FOR_APPROVAL,
                             WorkflowStatus.EXECUTING, WorkflowStatus.VERIFYING})


class VerificationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"  # verified: not processed
    PARTIAL = "PARTIAL"  # processed, but not as requested (e.g. different amount) — needs a human
    UNKNOWN = "UNKNOWN"  # could not be determined (yet)
    TIMEOUT = "TIMEOUT"  # the verification itself timed out


class ReasoningResult(BaseModel):
    """What the platform understood — structured, user-safe. Never the model's chain of thought."""

    reasoning_summary: str
    intent: Intent
    entities: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    required_information: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW


class VerificationResult(BaseModel):
    status: VerificationStatus
    method: str  # "status_lookup" | "state_check" | "response"
    tool: str | None = None
    reference: str | None = None
    detail: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)  # redacted
    attempts: int = 1
    checked_at: datetime = Field(default_factory=utcnow)


class StepResult(BaseModel):
    ok: bool
    data: Any = None  # redacted by the gateway
    error: str | None = None
    failure_kind: str | None = None
    failure_category: str | None = None
    policy_decision: str | None = None
    latency_ms: float | None = None
    attempts: int = 1
    execution_id: str | None = None


class ExecutionStep(BaseModel):
    id: str
    kind: StepKind = StepKind.TOOL
    origin: StepOrigin = StepOrigin.LLM
    tool: str | None = None
    call_id: str | None = None  # the LLM tool_call id this step answers (LLM-origin steps)
    arguments: dict[str, Any] = Field(default_factory=dict)  # LLM-visible arguments
    bindings: dict[str, str] = Field(default_factory=dict)  # bound param -> "<step_id>.<field>" (trusted template)
    bound_arguments: dict[str, Any] = Field(default_factory=dict)  # resolved from dependency outputs
    depends_on: list[str] = Field(default_factory=list)  # data edges: these must COMPLETE first
    after: list[str] = Field(default_factory=list)  # ordering edges: these must be finished (any outcome) first
    declared_depends_on: list[str] = Field(default_factory=list)  # tool names, from trusted tool registration
    # trusted scheduling metadata, copied from server-side tool registration at plan time
    operation_type: OperationType = OperationType.READ
    side_effect: SideEffect = SideEffect.NONE
    parallel_safe: bool = True
    idempotent: bool = True
    concurrency_group: str | None = None
    max_concurrency: int | None = None
    timeout_seconds: float | None = None
    template: str | None = None
    check: str | None = None  # CHECK steps: name of the deterministic check
    verifies: str | None = None  # VERIFY steps: the mutation step id
    status: StepStatus = StepStatus.PENDING
    status_reason: str | None = None
    idempotency_key: str | None = None
    action_hash: str | None = None
    hold_decision: str | None = None
    parallel_group: int | None = None  # wave number it ran in
    result: StepResult | None = None
    verification: VerificationResult | None = None
    output: dict[str, Any] = Field(default_factory=dict)  # CHECK outputs available to dependants
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def mutates(self) -> bool:
        return self.operation_type == OperationType.WRITE

    @property
    def financial(self) -> bool:
        return self.side_effect == SideEffect.FINANCIAL_MUTATION

    @property
    def all_arguments(self) -> dict[str, Any]:
        return {**self.arguments, **self.bound_arguments}


class WorkflowState(BaseModel):
    workflow_id: str = Field(default_factory=new_id)
    workflow_type: str = "agent_turn"
    tenant_id: str
    session_id: str
    conversation_id: str | None = None
    status: WorkflowStatus = WorkflowStatus.CREATED
    steps: list[ExecutionStep] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)  # intent, language, reasoning (structured, redacted)
    idempotency_key: str = Field(default_factory=lambda: new_id().replace("-", ""))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deadline: datetime | None = None  # absolute expiry of a held workflow
    version: int = 0
    worker_id: str | None = None
    reported: bool = True  # False while a terminal mutation outcome has not been told to the customer
    persistent: bool = False  # only workflows with mutations / holds are written to the database

    def step(self, step_id: str) -> ExecutionStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def next_step_id(self) -> str:
        return f"s{len(self.steps) + 1}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_step(self) -> str | None:
        running = [s.id for s in self.steps if s.status in (StepStatus.RUNNING, StepStatus.SUBMITTING, StepStatus.HELD)]
        return running[0] if running else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed_steps(self) -> list[str]:
        return [s.id for s in self.steps if s.status == StepStatus.COMPLETED]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_steps(self) -> list[str]:
        return [s.id for s in self.steps if s.status in (StepStatus.FAILED, StepStatus.UNKNOWN, StepStatus.REJECTED)]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pending_steps(self) -> list[str]:
        return [s.id for s in self.steps if s.status in (StepStatus.PENDING, StepStatus.HELD)]

    @property
    def has_mutation(self) -> bool:
        return any(s.mutates for s in self.steps)

    def mutation_steps(self) -> list[ExecutionStep]:
        return [s for s in self.steps if s.mutates and s.kind == StepKind.TOOL]


class ExecutionResult(BaseModel):
    """Structured aggregation of one execution pass. Per-call tool messages are derived from it — results are
    never flattened into one unstructured string."""

    workflow_id: str
    status: WorkflowStatus
    steps: list[ExecutionStep]
    waves: list[list[str]] = Field(default_factory=list)
    latency_ms: float = 0.0
    timed_out: bool = False

    def for_call(self, call_id: str) -> ExecutionStep | None:
        return next((s for s in self.steps if s.call_id == call_id), None)

    def held(self) -> list[ExecutionStep]:
        return [s for s in self.steps if s.status == StepStatus.HELD]

    def tool_message(self, step: ExecutionStep) -> str:
        """The structured content returned to the LLM for one of its tool calls."""
        r = step.result
        body: dict[str, Any] = {"ok": bool(r and r.ok) and step.status == StepStatus.COMPLETED, "status": step.status.value}
        if r and r.ok:
            body["data"] = r.data
        else:
            body["error"] = (r.error if r and r.error else step.status_reason) or step.status.value.lower()
            if r and r.policy_decision:
                body["policy_decision"] = r.policy_decision
            if r and r.failure_kind:
                body["outcome"] = r.failure_kind
        if step.verification:
            body["verification"] = {"status": step.verification.status.value, "reference": step.verification.reference}
        return json.dumps(body, default=str, ensure_ascii=False)
