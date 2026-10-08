"""Act + Verify: run an execution DAG in dependency order.

Scheduling (per pass):
  * a step is ready when its data dependencies COMPLETED and its ordering predecessors finished
  * ready read/verify steps run together as one *wave* (`asyncio.gather`, bounded by MAX_PARALLEL_TOOLS and the
    concurrency manager's global / group / tool / session slots)
  * a mutation runs alone: only when no other step is running and no other mutation is in flight or unverified
  * dependants of failed / skipped steps are SKIPPED; dependants of HELD steps wait for the hold to be resolved
Mutations:
  * write-ahead: the step is persisted as SUBMITTING (with its deterministic idempotency key) after the policy engine
    allowed it and before it is sent; recovery after a crash verifies, never resends
  * the submission runs in a shielded task: a voice barge-in cannot cancel a transfer half-way and lose its result
  * an ambiguous result (timeout / 5xx) makes the step UNKNOWN; it is never retried — its VERIFY step looks it up
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.execution.concurrency import ConcurrencyLimitTimeout, ToolConcurrencyManager
from app.agents.execution.models import (
    ExecutionResult,
    ExecutionStep,
    StepKind,
    StepOrigin,
    StepResult,
    StepStatus,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
    WorkflowStatus,
)
from app.agents.execution.templates import CHECKS, TEMPLATES
from app.agents.execution.verifier import Verifier
from app.agents.execution.workflow import InflightRegistry, WorkflowStore
from app.domain import PolicyDecisionType, RuntimeEvent, utcnow
from app.observability import metrics
from app.policies.approval import ActionGrant, context_action_hash
from app.security.redaction import RedactionProfile, redact_data
from app.tools.failures import FailureCategory
from app.tools.router import GatewayOutcome, ToolGateway
from app.tools.schemas import ToolCallRequest, ToolContext, ToolDefinition, ToolResult

log = logging.getLogger("app.agents.execution")
_FINISHED = frozenset({StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.REJECTED,
                       StepStatus.CANCELLED, StepStatus.UNKNOWN})
_BROKEN = frozenset({StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.REJECTED, StepStatus.CANCELLED, StepStatus.UNKNOWN})
_HOLD_STATUS = {PolicyDecisionType.REQUIRE_AUTH: WorkflowStatus.WAITING_FOR_AUTH,
                PolicyDecisionType.REQUIRE_CONFIRMATION: WorkflowStatus.WAITING_FOR_CONFIRMATION,
                PolicyDecisionType.REQUIRE_HUMAN_APPROVAL: WorkflowStatus.WAITING_FOR_APPROVAL}


class WriteAheadFailed(RuntimeError):
    pass


@dataclass
class StepEvent:
    """Emitted when a step finishes (or is held); the orchestrator turns it into runtime events."""

    step: ExecutionStep
    outcome: GatewayOutcome | None = None


@dataclass
class ExecutionContext:
    tool_ctx: Callable[[], ToolContext]  # fresh trusted context from the session state
    offered: dict[str, ToolDefinition]  # tools the model was offered this turn (LLM-origin steps)
    all_tools: dict[str, ToolDefinition]  # tenant registry (template helper / verification steps)
    allowed: set[str] | None
    deadline: float  # monotonic
    grants: dict[str, ActionGrant] = field(default_factory=dict)  # step id -> grant from customer/staff input
    outcomes: dict[str, GatewayOutcome] = field(default_factory=dict)


def idempotency_key(wf: WorkflowState, step: ExecutionStep, action_hash: str) -> str:
    """Deterministic per (workflow, step, exact action): a re-send of the same step carries the same key, so the
    institution de-duplicates it; a different amount/payee is a different action and gets a different key."""
    return hashlib.sha256(f"{wf.tenant_id}:{wf.idempotency_key}:{step.id}:{action_hash}".encode()).hexdigest()[:40]


class PlanExecutor:
    def __init__(self, *, gateway: ToolGateway, concurrency: ToolConcurrencyManager, verifier: Verifier,
                 store: WorkflowStore, inflight: InflightRegistry, max_parallel: int = 4, worker_id: str | None = None,
                 slow_ack_seconds: float = 1.2) -> None:
        self.gateway = gateway
        self.concurrency = concurrency
        self.verifier = verifier
        self.store = store
        self.inflight = inflight
        self.max_parallel = max_parallel
        self.worker_id = worker_id
        self.slow_ack_seconds = slow_ack_seconds

    # ------------------------------------------------------------------------------------------ scheduling
    @staticmethod
    def schedule(wf: WorkflowState) -> tuple[list[ExecutionStep], list[tuple[ExecutionStep, str]]]:
        """(ready steps, steps to skip with reason). Pure function of the workflow state."""
        by_id = {s.id: s for s in wf.steps}
        in_flight_mutation = any(s.mutates and s.kind == StepKind.TOOL and s.status in (StepStatus.RUNNING, StepStatus.SUBMITTING)
                                 for s in wf.steps)
        unresolved_mutation = [s for s in wf.steps if s.mutates and s.kind == StepKind.TOOL and s.status == StepStatus.UNKNOWN]
        ready, skip = [], []
        for s in wf.steps:
            if s.status != StepStatus.PENDING:
                continue
            if s.kind == StepKind.VERIFY:
                parent = by_id.get(s.verifies or "")
                if parent is None or parent.status in (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.REJECTED, StepStatus.CANCELLED):
                    skip.append((s, "nothing to verify"))
                elif parent.status in (StepStatus.COMPLETED, StepStatus.UNKNOWN):
                    ready.append(s)
                continue
            deps = [by_id[d] for d in s.depends_on if d in by_id]
            broken = next((d for d in deps if d.status in _BROKEN), None)
            if broken is not None:
                skip.append((s, broken.status_reason if broken.kind == StepKind.CHECK and broken.status_reason
                             else "an earlier step did not complete"))
                continue
            if not all(d.status == StepStatus.COMPLETED for d in deps):
                continue
            if not all(by_id[a].status in _FINISHED for a in s.after if a in by_id):
                continue
            if s.mutates and s.kind == StepKind.TOOL:
                if unresolved_mutation and any(u.id != s.id for u in unresolved_mutation):
                    skip.append((s, "an earlier account change has an unconfirmed outcome"))
                    continue
                if in_flight_mutation:
                    continue
            ready.append(s)
        return ready, skip

    def _wave(self, ready: list[ExecutionStep]) -> list[ExecutionStep]:
        reads = [s for s in ready if not (s.mutates and s.kind == StepKind.TOOL)]
        if reads:
            return reads[: self.max_parallel]
        return ready[:1]  # one mutation, alone

    # ------------------------------------------------------------------------------------------ run
    async def stream(self, wf: WorkflowState, ec: ExecutionContext) -> AsyncIterator[RuntimeEvent | StepEvent | ExecutionResult]:
        """Run until nothing more is ready. Yields runtime events / step events as they happen, then the result."""
        started = time.perf_counter()
        queue: asyncio.Queue = asyncio.Queue()
        waves: list[list[str]] = []
        done = object()
        timed_out = False
        wf.worker_id = self.worker_id
        if any(s.status == StepStatus.PENDING for s in wf.steps):
            wf.status = WorkflowStatus.EXECUTING

        async def runner() -> None:
            nonlocal timed_out
            wave_no = max((s.parallel_group or 0 for s in wf.steps), default=0)
            try:
                while True:
                    ready, skip = self.schedule(wf)
                    for s, why in skip:
                        s.status, s.status_reason, s.finished_at = StepStatus.SKIPPED, why, utcnow()
                        queue.put_nowait(StepEvent(s))
                    if not ready:
                        if not skip:
                            break
                        continue
                    if time.monotonic() >= ec.deadline:
                        timed_out = True
                        for s in wf.steps:
                            if s.status == StepStatus.PENDING:
                                s.status, s.status_reason = StepStatus.CANCELLED, "workflow timed out"
                                queue.put_nowait(StepEvent(s))
                        break
                    batch = self._wave(ready)
                    wave_no += 1
                    waves.append([s.id for s in batch])
                    mode = "parallel" if len(batch) > 1 else "sequential"
                    for s in batch:
                        s.parallel_group = wave_no
                        metrics.tool_execution_mode.labels(mode).inc()
                    await asyncio.gather(*(self._run_step(wf, s, ec, queue) for s in batch))
                    await self._save(wf)
            finally:
                queue.put_nowait(done)

        task = asyncio.create_task(runner())
        acked = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.slow_ack_seconds) if not acked else await queue.get()
                except TimeoutError:
                    acked = True
                    running = [s for s in wf.steps if s.status in (StepStatus.RUNNING, StepStatus.SUBMITTING)]
                    if running:
                        yield RuntimeEvent(type="workflow.progress", data={
                            "workflow_id": wf.workflow_id, "phase": "slow",
                            "mutation": any(s.mutates for s in running), "steps": [s.tool for s in running]})
                    continue
                if item is done:
                    break
                yield item
            await task
        finally:
            if not task.done():
                task.cancel()  # reads are cancelled; mutation submissions are shielded and finish on their own
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._finalize_status(wf, timed_out)
        await self._save(wf)
        latency = (time.perf_counter() - started) * 1000
        metrics.workflow_latency.labels(wf.workflow_type).observe(latency / 1000)
        yield ExecutionResult(workflow_id=wf.workflow_id, status=wf.status, steps=list(wf.steps), waves=waves,
                              latency_ms=round(latency, 1), timed_out=timed_out)

    async def _run_step(self, wf: WorkflowState, s: ExecutionStep, ec: ExecutionContext, queue: asyncio.Queue) -> None:
        s.status, s.started_at = StepStatus.RUNNING, utcnow()
        if s.kind == StepKind.TOOL:  # a resumed (previously held) step starts from a clean slate
            s.result, s.status_reason, s.hold_decision, s.submitted_at = None, None, None, None
        try:
            if s.kind == StepKind.CHECK:
                self._run_check(wf, s)
            elif s.kind == StepKind.VERIFY:
                queue.put_nowait(RuntimeEvent(type="workflow.progress", data={"workflow_id": wf.workflow_id, "phase": "verifying",
                                                                              "step_id": s.id, "tool": s.tool}))
                await self._run_verify(wf, s, ec)
            else:
                queue.put_nowait(RuntimeEvent(type="tool.started", tool=s.tool, data={"step_id": s.id, "workflow_id": wf.workflow_id,
                                                                                      "parallel_group": s.parallel_group}))
                await self._run_tool(wf, s, ec, queue)
        except Exception:  # noqa: BLE001 - a crashed step must not take the workflow down
            log.exception("step crashed", extra={"workflow_id": wf.workflow_id, "step_id": s.id})
            if s.status in (StepStatus.RUNNING,):
                s.status = StepStatus.FAILED
                s.result = StepResult(ok=False, error="internal error", failure_category=FailureCategory.UNKNOWN.value)
            # SUBMITTING stays SUBMITTING: the write may have been sent; reconciliation verifies it
        s.finished_at = utcnow()
        self._log_step(wf, s)
        queue.put_nowait(StepEvent(s, ec.outcomes.get(s.id)))

    def _run_check(self, wf: WorkflowState, s: ExecutionStep) -> None:
        fn = CHECKS[s.check or ""]
        deps = {d: wf.step(d) for d in s.depends_on}
        ok, out, err = fn(s, {k: v for k, v in deps.items() if v is not None})
        s.output = out
        s.status = StepStatus.COMPLETED if ok else StepStatus.FAILED
        s.result = StepResult(ok=ok, data=out if ok else None, error=err,
                              failure_category=None if ok else FailureCategory.BUSINESS_RULE_FAILURE.value,
                              failure_kind=None if ok else "not_executed")
        s.status_reason = err

    async def _run_tool(self, wf: WorkflowState, s: ExecutionStep, ec: ExecutionContext, queue: asyncio.Queue) -> None:
        for param, ref in s.bindings.items():
            sid, fld = ref.split(".", 1)
            src = wf.step(sid)
            value = src.output.get(fld) if src else None
            if value is None:
                s.status, s.status_reason = StepStatus.FAILED, f"could not resolve {param}"
                s.result = StepResult(ok=False, error=s.status_reason, failure_kind="not_executed",
                                      failure_category=FailureCategory.DEPENDENCY_UNAVAILABLE.value)
                return
            s.bound_arguments[param] = value
        ctx = ec.tool_ctx()
        ctx.workflow_id, ctx.step_id = wf.workflow_id, s.id
        tool_def = ec.offered.get(s.tool or "") if s.origin == StepOrigin.LLM else ec.all_tools.get(s.tool or "")
        offered = ec.offered if s.origin == StepOrigin.LLM else ({s.tool: tool_def} if tool_def else {})
        allowed = ec.allowed
        if s.origin == StepOrigin.TEMPLATE and allowed is not None:
            # a helper step of a trusted template is permitted exactly when the agent may perform the action itself
            action = next((t.action_tool for t in TEMPLATES.values() if t.name == s.template), None)
            if action in allowed:
                allowed = allowed | {s.tool or ""}
        if s.mutates and tool_def is not None:
            clean, _ = self.gateway._clean_args(tool_def, s.arguments)  # noqa: SLF001 - same normalisation as the gateway
            s.action_hash = context_action_hash(tool_def.name, {**clean, **s.bound_arguments}, ctx)
            s.idempotency_key = ctx.idempotency_key = idempotency_key(wf, s, s.action_hash)
        call = ToolCallRequest(id=s.call_id or f"{wf.workflow_id[:8]}:{s.id}", name=s.tool or "", arguments=s.arguments)

        async def write_ahead(decision) -> None:
            if s.mutates:
                s.status, s.submitted_at = StepStatus.SUBMITTING, utcnow()
                wf.persistent, wf.reported = True, False  # reported again only once the customer is told the outcome
                try:
                    await self.store.save(wf)  # must be durable BEFORE the request leaves; otherwise do not send
                except Exception as e:
                    s.status, s.submitted_at = StepStatus.RUNNING, None
                    raise WriteAheadFailed(str(e)) from e
                queue.put_nowait(RuntimeEvent(type="workflow.progress", tool=s.tool, data={
                    "workflow_id": wf.workflow_id, "phase": "submitting", "step_id": s.id}))

        async def submit() -> GatewayOutcome:
            remaining = max(0.05, ec.deadline - time.monotonic())
            try:
                async with self.concurrency.slot(tenant_id=wf.tenant_id, session_id=wf.session_id, tool=s.tool or "",
                                                 group=s.concurrency_group, tool_limit=s.max_concurrency, timeout=remaining,
                                                 lease_ttl=(s.timeout_seconds or 10) + 5):
                    outcome = await self.gateway.execute(call, ctx, offered=offered, allowed_tools=allowed,
                                                         grant=ec.grants.get(s.id), bound_args=s.bound_arguments,
                                                         deadline=ec.deadline, on_allowed=write_ahead)
            except WriteAheadFailed:
                log.exception("write-ahead persistence failed; mutation not sent", extra={"workflow_id": wf.workflow_id})
                outcome = GatewayOutcome(call=call, tool=tool_def, decision=None, arguments=s.arguments, execution_id="-",
                                         result=ToolResult(ok=False, error="Could not record the request safely; nothing was sent.",
                                                           failure_kind="not_executed",
                                                           failure_category=FailureCategory.DEPENDENCY_UNAVAILABLE.value))
            except ConcurrencyLimitTimeout as e:
                outcome = GatewayOutcome(call=call, tool=tool_def, decision=None, arguments=s.arguments, execution_id="-",
                                         result=ToolResult(ok=False, error="The service is busy right now; nothing was sent.",
                                                           failure_kind="not_executed",
                                                           failure_category=FailureCategory.RATE_LIMIT.value))
                log.warning("concurrency slot timeout", extra={"scope": e.scope, "tool": s.tool})
            self._apply_outcome(wf, s, outcome)
            ec.outcomes[s.id] = outcome
            if s.mutates:
                await self._save(wf)  # recorded even if the turn that started it was cancelled
            return outcome

        if s.mutates:
            task = asyncio.create_task(submit())
            self.inflight.add(wf.session_id, task)
            await asyncio.shield(task)
        else:
            await submit()

    @staticmethod
    def _apply_outcome(wf: WorkflowState, s: ExecutionStep, o: GatewayOutcome) -> None:
        r = o.result
        s.result = StepResult(ok=r.ok, data=r.data, error=r.error, failure_kind=r.failure_kind, failure_category=r.failure_category,
                              policy_decision=r.policy_decision, latency_ms=r.latency_ms, attempts=r.attempts,
                              execution_id=o.execution_id)
        if o.decision is not None:
            s.action_hash = o.decision.action_hash
        status = o.status
        if status == "pending":
            s.status, s.hold_decision = StepStatus.HELD, o.decision.decision.value if o.decision else None
            s.status_reason = o.decision.reason if o.decision else None
        elif status == "completed":
            s.status = StepStatus.COMPLETED
        elif s.mutates and r.failure_kind == "unknown":
            s.status, s.status_reason = StepStatus.UNKNOWN, "outcome not confirmed by the institution"
        else:
            s.status, s.status_reason = StepStatus.FAILED, r.error

    async def _run_verify(self, wf: WorkflowState, s: ExecutionStep, ec: ExecutionContext) -> None:
        parent = wf.step(s.verifies or "")
        assert parent is not None
        tpl = TEMPLATES.get(parent.tool or "")
        spec = tpl.verify if tpl and tpl.verify_tool(ec.all_tools) else None
        prev = wf.status
        wf.status = WorkflowStatus.VERIFYING
        ctx = ec.tool_ctx()
        ctx.workflow_id, ctx.step_id = wf.workflow_id, s.id

        async def lookup(tool: str, args: dict[str, Any]) -> ToolResult:
            # platform-initiated read: authentication policy still applies; the agent's tool allow-list does not
            return await self.gateway.verify(tool, args, ctx, offered=ec.all_tools, allowed_tools=None)

        vr = await self.verifier.verify(parent, spec, lookup)
        wf.status = prev
        apply_verification(parent, vr)
        s.verification = parent.verification
        s.status = StepStatus.COMPLETED
        s.result = StepResult(ok=True, data=redact_data(vr.evidence, RedactionProfile.AUDIT))

    # ------------------------------------------------------------------------------------------ helpers
    @staticmethod
    def _finalize_status(wf: WorkflowState, timed_out: bool) -> None:
        held = [s for s in wf.steps if s.status == StepStatus.HELD]
        if held:
            wf.status = _HOLD_STATUS.get(PolicyDecisionType(held[0].hold_decision), WorkflowStatus.WAITING_FOR_AUTH) \
                if held[0].hold_decision else WorkflowStatus.WAITING_FOR_AUTH
            return
        muts = wf.mutation_steps()
        if any(m.status in (StepStatus.UNKNOWN, StepStatus.SUBMITTING) for m in muts):
            pending = any(m.verification and m.verification.detail == "still processing" for m in muts)
            wf.status = WorkflowStatus.VERIFYING if pending else WorkflowStatus.ESCALATED
            wf.reported = False
        elif any(s.status == StepStatus.PENDING for s in wf.steps):
            wf.status = WorkflowStatus.EXECUTING
        elif muts and all(m.status in (StepStatus.FAILED, StepStatus.REJECTED, StepStatus.SKIPPED) for m in muts) and \
                any(m.status == StepStatus.FAILED for m in muts):
            wf.status = WorkflowStatus.FAILED
        elif timed_out or (not muts and wf.steps and all(s.status != StepStatus.COMPLETED for s in wf.steps)):
            wf.status = WorkflowStatus.FAILED
        else:
            wf.status = WorkflowStatus.COMPLETED
        if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.ESCALATED):
            metrics.workflow_outcomes.labels(wf.workflow_type, wf.status.value).inc()

    async def _save(self, wf: WorkflowState) -> None:
        try:
            await self.store.save(wf)
        except Exception:
            log.exception("workflow persistence failed", extra={"workflow_id": wf.workflow_id})

    def _log_step(self, wf: WorkflowState, s: ExecutionStep) -> None:
        r = s.result
        log.info("workflow step finished", extra={
            "workflow_id": wf.workflow_id, "session_id": wf.session_id, "tool_call_id": s.call_id, "step_id": s.id,
            "tool_name": s.tool, "worker_id": self.worker_id, "execution_status": s.status.value,
            "latency_ms": r.latency_ms if r else None, "parallel_group": s.parallel_group,
            "dependency_count": len(s.depends_on) + len(s.after), "retry_count": max(0, (r.attempts if r else 1) - 1),
            "policy_decision": (r.policy_decision if r else None) or s.hold_decision or ("ALLOW" if r and r.ok else None),
            "verification_status": s.verification.status.value if s.verification else None,
            "failure_category": r.failure_category if r else None})


def apply_verification(parent: ExecutionStep, vr: VerificationResult) -> None:
    """Fold a verification result into the mutation step. Nothing is ever retried from here.

    explicit success response + lookup SUCCESS              -> COMPLETED (verified)
    explicit success response + lookup unavailable          -> COMPLETED, verified by the response itself
    explicit success response + lookup FAILED / PARTIAL     -> UNKNOWN (contradiction: a human must look)
    ambiguous (UNKNOWN) + lookup SUCCESS                    -> COMPLETED (recovered; real receipt from the bank)
    ambiguous + lookup FAILED (no record after the window)  -> FAILED, verified not processed
    ambiguous + lookup PARTIAL / UNKNOWN / TIMEOUT          -> stays UNKNOWN -> escalate, never resubmit
    """
    was_success = parent.status == StepStatus.COMPLETED
    if was_success:
        if vr.status in (VerificationStatus.UNKNOWN, VerificationStatus.TIMEOUT):
            vr = vr.model_copy(update={"status": VerificationStatus.SUCCESS, "method": "response",
                                       "detail": "confirmed by the institution's response; status lookup unavailable"})
        elif vr.status in (VerificationStatus.FAILED, VerificationStatus.PARTIAL):
            vr = vr.model_copy(update={"status": VerificationStatus.PARTIAL,
                                       "detail": "status lookup contradicts the institution's response"})
    parent.verification = vr
    if vr.status == VerificationStatus.SUCCESS:
        if not was_success:  # recovered: the timed-out request did go through
            parent.status, parent.status_reason = StepStatus.COMPLETED, "confirmed by status lookup"
            parent.result = StepResult(ok=True, data=dict(vr.evidence), attempts=parent.result.attempts if parent.result else 1,
                                       latency_ms=parent.result.latency_ms if parent.result else None,
                                       execution_id=parent.result.execution_id if parent.result else None)
    elif vr.status == VerificationStatus.FAILED:
        parent.status, parent.status_reason = StepStatus.FAILED, vr.detail or "not processed"
        if parent.result:
            parent.result.ok, parent.result.failure_kind = False, "rejected"  # verified: nothing was processed
    else:  # PARTIAL / UNKNOWN / TIMEOUT
        parent.status, parent.status_reason = StepStatus.UNKNOWN, vr.detail or "outcome could not be confirmed"
        if parent.result:
            parent.result.ok, parent.result.failure_kind = False, "unknown"
