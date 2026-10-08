"""Unified Tool Gateway.

Every tool call — whether the backing system is REST, OpenAPI, MCP, a custom adapter or a built-in —
passes through `ToolGateway.execute`, which enforces, in order:

  1. the tool exists and was offered to the model this turn (blocks invented / smuggled tool names)
  2. argument hygiene: platform-injected params (customer_id, ...) are stripped from LLM input and filled
     from the trusted session context; remaining args are JSON-schema validated
  3. the Policy Engine decision (auth, risk, approval, confirmation)
  4. execution with a per-attempt timeout; automatic retries only where the tool's *trusted* execution
     metadata allows it (reads; idempotent non-financial writes) — see app.tools.failures
  5. output redaction, ToolExecution persistence and audit

Workflow-bound parameters (e.g. a beneficiary_id resolved by an earlier step) arrive separately as
`bound_args` from the execution engine; the same names in LLM output are stripped and audited.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import jsonschema
from pydantic import BaseModel, ConfigDict

from app.database.models import ToolExecution
from app.database.session import Database
from app.domain import PolicyDecisionType, RiskLevel, new_id
from app.observability import metrics
from app.observability.tracing import span
from app.policies.approval import ActionGrant
from app.policies.engine import PolicyDecision, PolicyEngine
from app.security.audit import AuditLogger
from app.security.redaction import RedactionProfile, redact_data
from app.tools.failures import FailureCategory, backoff_delay, category_for_status, may_retry
from app.tools.registry import ToolRegistry
from app.tools.schemas import (
    OperationType,
    ToolCallRequest,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolResult,
    ToolSource,
)

log = logging.getLogger(__name__)


_DEFINITE = frozenset({FailureCategory.VALIDATION_ERROR, FailureCategory.AUTHENTICATION_ERROR, FailureCategory.AUTHORIZATION_ERROR,
                       FailureCategory.BUSINESS_RULE_FAILURE, FailureCategory.NOT_FOUND, FailureCategory.RATE_LIMIT})


@dataclass(frozen=True)
class GatewayLimits:
    default_tool_timeout: float = 10.0
    read_retry_count: int = 2
    write_retry_count: int = 1  # idempotent, non-financial writes with an idempotency key only
    retry_base_seconds: float = 0.2
    retry_cap_seconds: float = 2.0


class ToolExecutor(Protocol):
    async def execute(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext) -> Any: ...


class GatewayOutcome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    call: ToolCallRequest
    tool: ToolDefinition | None
    decision: PolicyDecision | None
    result: ToolResult
    arguments: dict[str, Any]  # validated LLM-visible args (no injected values)
    execution_id: str

    @property
    def status(self) -> str:
        if self.decision and self.decision.decision not in (PolicyDecisionType.ALLOW, PolicyDecisionType.DENY):
            return "pending"
        if (self.decision and self.decision.decision == PolicyDecisionType.DENY) or self.result.policy_decision == "DENY":
            return "denied"
        return "completed" if self.result.ok else "failed"


class ToolGateway:
    def __init__(self, registry: ToolRegistry, policy: PolicyEngine, db: Database, audit: AuditLogger,
                 executors: dict[ToolSource, ToolExecutor], limits: GatewayLimits | None = None) -> None:
        self.registry = registry
        self.policy = policy
        self.db = db
        self.audit = audit
        self.executors = executors
        self.limits = limits or GatewayLimits()

    async def evaluate(self, call: ToolCallRequest, ctx: ToolContext, *, offered: dict[str, ToolDefinition],
                       allowed_tools: set[str] | None = None, grant: ActionGrant | None = None,
                       bound_args: dict[str, Any] | None = None) -> PolicyDecision | None:
        """Dry-run of steps 1-3 (no execution, no persistence): used for plan-level policy checks."""
        tool = offered.get(call.name)
        if tool is None:
            return None
        args, _ = self._clean_args(tool, call.arguments)
        try:
            jsonschema.validate(args, tool.llm_schema())
        except jsonschema.ValidationError:
            return None
        args.update(self._trusted_bound(tool, bound_args))
        return await self.policy.evaluate(tool, args, ctx, allowed_tools=allowed_tools, grant=grant)

    @staticmethod
    def _clean_args(tool: ToolDefinition, raw: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        args = dict(raw)
        smuggled = {k for k in (*tool.injected_params, *tool.bound_params) if k in args}
        for k in smuggled:
            args.pop(k)
        return args, smuggled

    @staticmethod
    def _trusted_bound(tool: ToolDefinition, bound_args: dict[str, Any] | None) -> dict[str, Any]:
        return {k: v for k, v in (bound_args or {}).items() if k in tool.bound_params and v is not None}

    async def execute(self, call: ToolCallRequest, ctx: ToolContext, *, offered: dict[str, ToolDefinition],
                      allowed_tools: set[str] | None = None, grant: ActionGrant | None = None,
                      bound_args: dict[str, Any] | None = None, deadline: float | None = None,
                      on_allowed: Callable[[PolicyDecision], Awaitable[None]] | None = None) -> GatewayOutcome:
        """`bound_args`: trusted values from earlier workflow steps (never LLM output). `deadline`: monotonic time
        after which no new attempt starts (the workflow timeout). `on_allowed`: awaited after the policy engine
        allowed the call and before anything is sent (write-ahead persistence of mutations)."""
        exec_id = new_id()
        tool = offered.get(call.name)
        if tool is None:
            known = await self.registry.get(ctx.tenant_id, call.name)
            reason = "Tool was not offered for this request." if known else "Unknown tool."
            await self.audit.record(ctx.tenant_id, "security.tool_not_offered", session_id=ctx.session_id,
                                    channel=ctx.channel.value, resource=call.name, outcome="blocked")
            metrics.tool_calls.labels(ctx.channel.value, call.name[:64], "rejected").inc()
            return GatewayOutcome(call=call, tool=None, decision=None, arguments={}, execution_id=exec_id,
                                  result=ToolResult(ok=False, error=reason, policy_decision="DENY", failure_kind="not_executed"))

        args, smuggled = self._clean_args(tool, call.arguments)
        if smuggled:
            await self.audit.record(ctx.tenant_id, "security.injected_param_override_attempt", session_id=ctx.session_id,
                                    channel=ctx.channel.value, resource=tool.name, outcome="blocked",
                                    payload={"params": sorted(smuggled)})
        try:
            jsonschema.validate(args, tool.llm_schema())
        except jsonschema.ValidationError as e:
            return await self._finish(call, tool, ctx, None, args, exec_id,
                                      ToolResult(ok=False, error=f"Invalid arguments: {e.message[:200]}", failure_kind="not_executed",
                                                 failure_category=FailureCategory.VALIDATION_ERROR))
        args.update(self._trusted_bound(tool, bound_args))

        with span("policy.evaluate", **{"tool.name": tool.name, "tool.risk": tool.risk_level.value}):
            decision = await self.policy.evaluate(tool, args, ctx, allowed_tools=allowed_tools, grant=grant)
        metrics.policy_decisions.labels(ctx.channel.value, decision.decision.value, decision.risk_level.value).inc()
        if decision.decision != PolicyDecisionType.ALLOW:
            metrics.policy_blocks.labels("step", decision.decision.value).inc()
            return await self._finish(call, tool, ctx, decision, args, exec_id,
                                      ToolResult(ok=False, error=decision.reason, policy_decision=decision.decision.value,
                                                 failure_kind="not_executed", failure_category=FailureCategory.POLICY_BLOCKED))

        if on_allowed is not None:
            await on_allowed(decision)
        full_args = {**args, **{p: ctx.value_for(key) for p, key in tool.injected_params.items()}}
        result = await self._run(tool, full_args, ctx, deadline=deadline)
        return await self._finish(call, tool, ctx, decision, args, exec_id, result)

    async def verify(self, name: str, args: dict[str, Any], ctx: ToolContext, *, offered: dict[str, ToolDefinition] | None = None,
                     allowed_tools: set[str] | None = None) -> ToolResult:
        """Platform-initiated, read-only status check used to verify a mutation's outcome (never LLM-initiated).

        Internal verification tools (e.g. get_transfer_status by idempotency key) skip the policy engine like OTP
        verification does; customer-facing read tools (e.g. get_card_status) still go through it. Mutating tools are
        refused outright: verification can never cause a side effect."""
        tool = (offered or {}).get(name) or await self.registry.get(ctx.tenant_id, name)
        if tool is None:
            return ToolResult(ok=False, error=f"{name} is not configured", failure_kind="not_executed",
                              failure_category=FailureCategory.DEPENDENCY_UNAVAILABLE)
        if tool.exec.mutates or tool.exec.operation_type == OperationType.WRITE:
            return ToolResult(ok=False, error="verification tools must be read-only", failure_kind="not_executed",
                              failure_category=FailureCategory.POLICY_BLOCKED)
        clean = {k: v for k, v in args.items() if k not in tool.injected_params}
        decision = None
        if not tool.internal:
            decision = await self.policy.evaluate(tool, clean, ctx, allowed_tools=allowed_tools)
            if not decision.allowed:
                return ToolResult(ok=False, error=decision.reason, policy_decision=decision.decision.value,
                                  failure_kind="not_executed", failure_category=FailureCategory.POLICY_BLOCKED)
        full_args = {**clean, **{p: ctx.value_for(key) for p, key in tool.injected_params.items()}}
        result = await self._run(tool, full_args, ctx)
        await self._persist(tool, ctx, decision, clean, result, new_id())
        return result

    async def execute_internal(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Platform-initiated calls (OTP send/verify). Never reachable from LLM output."""
        tool = await self.registry.get(ctx.tenant_id, name)
        if tool is None:
            return ToolResult(ok=False, error=f"{name} is not configured for this institution", failure_kind="not_executed")
        full_args = {**args, **{p: ctx.value_for(key) for p, key in tool.injected_params.items()}}
        result = await self._run(tool, full_args, ctx, redact_output=False)  # consumed by the platform, not the LLM
        await self._persist(tool, ctx, None, args, result, new_id())
        return result

    async def _run(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext, redact_output: bool = True,
                   deadline: float | None = None) -> ToolResult:
        started = time.perf_counter()
        ex = tool.exec
        timeout = tool.timeout_seconds or self.limits.default_tool_timeout
        max_retries = 0
        if not ex.mutates:
            max_retries = self.limits.read_retry_count
        elif ex.idempotent and ctx.idempotency_key:
            max_retries = self.limits.write_retry_count
        attempt = 0
        with span("tool.execute", **{"tool.name": tool.name, "tool.source": tool.source.value, "tenant.id": ctx.tenant_id,
                                     "channel": ctx.channel.value, "tool.operation": ex.operation_type.value}) as sp:
            while True:
                attempt += 1
                data, err, category, sent, retry_after, status_code = None, None, None, None, None, None
                try:
                    data = await asyncio.wait_for(self._invoke(tool, args, ctx), timeout=timeout)
                    ok = True
                except TimeoutError:
                    ok, err, category = False, f"{tool.name} timed out after {timeout:g}s", FailureCategory.TIMEOUT
                    metrics.tool_timeouts.labels(tool.name).inc()
                except ToolExecutionError as e:
                    ok, err, sent, retry_after, status_code = False, str(e), e.sent, e.retry_after, e.status_code
                    category = _category(e)
                    sp.set_attribute("tool.error", type(e).__name__)
                except Exception as e:  # noqa: BLE001 - external systems fail in creative ways
                    log.exception("tool execution crashed", extra={"tool": tool.name})
                    ok, err, category = False, f"{type(e).__name__}", FailureCategory.UNKNOWN
                if ok:
                    break
                assert category is not None
                remaining = None if deadline is None else deadline - time.monotonic()
                delay = backoff_delay(attempt - 1, base=self.limits.retry_base_seconds, cap=self.limits.retry_cap_seconds,
                                      retry_after=retry_after)
                if (attempt > max_retries or not may_retry(category, ex, sent=sent, has_idempotency_key=bool(ctx.idempotency_key))
                        or (remaining is not None and remaining < delay + timeout)):
                    break
                metrics.tool_retries.labels(tool.name, category.value).inc()
                await asyncio.sleep(delay)
        latency = (time.perf_counter() - started) * 1000
        metrics.tool_latency.labels(ctx.channel.value, tool.name, tool.source.value).observe(latency / 1000)
        metrics.tool_calls.labels(ctx.channel.value, tool.name, "success" if ok else "failure").inc()
        if ok:
            # The LLM never sees full identifiers or authentication secrets, even if the bank API returns them.
            if redact_output:
                data = redact_data(data, RedactionProfile.AUDIT)
            return ToolResult(ok=True, data=data, latency_ms=round(latency, 1), attempts=attempt)
        metrics.tool_failures.labels(tool.name, category.value).inc()
        # not_executed: provably never sent; rejected: the institution answered "no" (nothing processed); unknown: verify
        kind = "not_executed" if sent is False else "rejected" if category in _DEFINITE else "unknown"
        return ToolResult(ok=False, error=err, latency_ms=round(latency, 1), failure_kind=kind, failure_category=category.value,
                          status_code=status_code, attempts=attempt)

    async def _invoke(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext) -> Any:
        if tool.source == ToolSource.BUILTIN:
            handler = self.registry.builtin_handler(tool.name)
            if handler is None:
                raise ToolExecutionError("built-in handler missing", sent=False)
            return await handler(args, ctx)
        executor = self.executors.get(ToolSource.REST if tool.source == ToolSource.OPENAPI else tool.source)
        if executor is None:
            raise ToolExecutionError(f"no executor for {tool.source.value}", sent=False)
        return await executor.execute(tool, args, ctx)

    async def _finish(self, call, tool, ctx, decision, args, exec_id, result: ToolResult) -> GatewayOutcome:
        await self._persist(tool, ctx, decision, args, result, exec_id)
        if decision and tool.risk_level.level >= RiskLevel.MEDIUM.level:
            await self.audit.record(
                ctx.tenant_id, "tool.decision" if not decision.allowed else "tool.executed",
                session_id=ctx.session_id, conversation_id=ctx.conversation_id, channel=ctx.channel.value,
                resource=tool.name, outcome=decision.decision.value if not decision.allowed else ("success" if result.ok else "failure"),
                payload={"args": args, "risk_level": decision.risk_level.value, "risk_score": decision.risk_score,
                         "risk_factors": decision.risk_factors, "rules": decision.matched_rules, "reason": decision.reason},
            )
        return GatewayOutcome(call=call, tool=tool, decision=decision, result=result, arguments=args, execution_id=exec_id)

    async def _persist(self, tool, ctx, decision, args, result: ToolResult, exec_id: str) -> None:
        status = ("completed" if result.ok else "failed") if decision is None or decision.allowed else (
            "denied" if decision.decision == PolicyDecisionType.DENY else "pending")
        try:
            async with self.db.session() as s:
                s.add(ToolExecution(
                    id=exec_id, tenant_id=ctx.tenant_id, conversation_id=ctx.conversation_id, session_id=ctx.session_id,
                    tool_name=tool.name, source_type=tool.source.value, channel=ctx.channel.value,
                    arguments=redact_data(args, RedactionProfile.AUDIT),
                    result=redact_data(result.data if isinstance(result.data, dict) else {"value": result.data}, RedactionProfile.AUDIT)
                    if result.ok else {},
                    status=status, policy_decision=decision.decision.value if decision else None,
                    policy_reason=decision.reason if decision else None,
                    risk_level=decision.risk_level.value if decision else tool.risk_level.value,
                    risk_score=decision.risk_score if decision else None, latency_ms=result.latency_ms, error=result.error,
                    attempts=result.attempts, workflow_id=ctx.workflow_id, step_id=ctx.step_id,
                    idempotency_key=ctx.idempotency_key, failure_category=result.failure_category,
                ))
        except Exception:
            log.exception("failed to persist tool execution")


def _category(e: ToolExecutionError) -> FailureCategory:
    if e.category:
        try:
            return FailureCategory(e.category)
        except ValueError:
            pass
    if e.status_code is not None:
        return category_for_status(e.status_code)
    return FailureCategory.NETWORK_ERROR if e.retryable else FailureCategory.BUSINESS_RULE_FAILURE
