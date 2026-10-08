"""Unified Tool Gateway.

Every tool call — whether the backing system is REST, OpenAPI, MCP, a custom adapter or a built-in —
passes through `ToolGateway.execute`, which enforces, in order:

  1. the tool exists and was offered to the model this turn (blocks invented / smuggled tool names)
  2. argument hygiene: platform-injected params (customer_id, ...) are stripped from LLM input and filled
     from the trusted session context; remaining args are JSON-schema validated
  3. the Policy Engine decision (auth, risk, approval, confirmation)
  4. execution with timeout/retry, tracing and metrics
  5. output redaction, ToolExecution persistence and audit
"""

from __future__ import annotations

import logging
import time
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
from app.tools.registry import ToolRegistry
from app.tools.schemas import (
    ToolCallRequest,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolResult,
    ToolSource,
)

log = logging.getLogger(__name__)


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
                 executors: dict[ToolSource, ToolExecutor]) -> None:
        self.registry = registry
        self.policy = policy
        self.db = db
        self.audit = audit
        self.executors = executors

    async def execute(self, call: ToolCallRequest, ctx: ToolContext, *, offered: dict[str, ToolDefinition],
                      allowed_tools: set[str] | None = None, grant: ActionGrant | None = None) -> GatewayOutcome:
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

        args = dict(call.arguments)
        smuggled = {k for k in tool.injected_params if k in args}
        for k in smuggled:
            args.pop(k)
        if smuggled:
            await self.audit.record(ctx.tenant_id, "security.injected_param_override_attempt", session_id=ctx.session_id,
                                    channel=ctx.channel.value, resource=tool.name, outcome="blocked",
                                    payload={"params": sorted(smuggled)})
        try:
            jsonschema.validate(args, tool.llm_schema())
        except jsonschema.ValidationError as e:
            return await self._finish(call, tool, ctx, None, args, exec_id,
                                      ToolResult(ok=False, error=f"Invalid arguments: {e.message[:200]}", failure_kind="not_executed"))

        with span("policy.evaluate", **{"tool.name": tool.name, "tool.risk": tool.risk_level.value}):
            decision = await self.policy.evaluate(tool, args, ctx, allowed_tools=allowed_tools, grant=grant)
        metrics.policy_decisions.labels(ctx.channel.value, decision.decision.value, decision.risk_level.value).inc()
        if decision.decision != PolicyDecisionType.ALLOW:
            return await self._finish(call, tool, ctx, decision, args, exec_id,
                                      ToolResult(ok=False, error=decision.reason, policy_decision=decision.decision.value,
                                                 failure_kind="not_executed"))

        full_args = {**args, **{p: ctx.value_for(key) for p, key in tool.injected_params.items()}}
        result = await self._run(tool, full_args, ctx)
        return await self._finish(call, tool, ctx, decision, args, exec_id, result)

    async def execute_internal(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Platform-initiated calls (OTP send/verify). Never reachable from LLM output."""
        tool = await self.registry.get(ctx.tenant_id, name)
        if tool is None:
            return ToolResult(ok=False, error=f"{name} is not configured for this institution", failure_kind="not_executed")
        full_args = {**args, **{p: ctx.value_for(key) for p, key in tool.injected_params.items()}}
        result = await self._run(tool, full_args, ctx, redact_output=False)  # consumed by the platform, not the LLM
        await self._persist(tool, ctx, None, args, result, new_id())
        return result

    async def _run(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext, redact_output: bool = True) -> ToolResult:
        started = time.perf_counter()
        with span("tool.execute", **{"tool.name": tool.name, "tool.source": tool.source.value, "tenant.id": ctx.tenant_id,
                                     "channel": ctx.channel.value}) as sp:
            try:
                if tool.source == ToolSource.BUILTIN:
                    handler = self.registry.builtin_handler(tool.name)
                    if handler is None:
                        raise ToolExecutionError("built-in handler missing")
                    data = await handler(args, ctx)
                else:
                    executor = self.executors.get(ToolSource.REST if tool.source == ToolSource.OPENAPI else tool.source)
                    if executor is None:
                        raise ToolExecutionError(f"no executor for {tool.source.value}")
                    data = await executor.execute(tool, args, ctx)
                ok, err, kind = True, None, None
            except ToolExecutionError as e:
                data, ok, err = None, False, str(e)
                definite = (e.status_code is not None and 400 <= e.status_code < 500 and e.status_code != 429) or not e.retryable
                kind = "rejected" if definite else "unknown"
                sp.set_attribute("tool.error", type(e).__name__)
            except Exception as e:  # noqa: BLE001 - external systems fail in creative ways
                log.exception("tool execution crashed", extra={"tool": tool.name})
                data, ok, err, kind = None, False, f"{type(e).__name__}", "unknown"
        latency = (time.perf_counter() - started) * 1000
        metrics.tool_latency.labels(ctx.channel.value, tool.name, tool.source.value).observe(latency / 1000)
        metrics.tool_calls.labels(ctx.channel.value, tool.name, "success" if ok else "failure").inc()
        # The LLM never sees full identifiers or authentication secrets, even if the bank API returns them.
        if ok and redact_output:
            data = redact_data(data, RedactionProfile.AUDIT)
        return ToolResult(ok=ok, data=data if ok else None, error=err, latency_ms=round(latency, 1), failure_kind=kind)

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
                ))
        except Exception:
            log.exception("failed to persist tool execution")
