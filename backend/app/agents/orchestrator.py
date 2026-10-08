"""Turn orchestration. Channel-agnostic: chat and voice turns run through exactly this code.

One turn:
  lock session (all workers) -> language detection -> secret stripping -> persist user message
  -> [handed off?  relay to human]
  -> [unverified mutation from an earlier turn?  RECONCILE: verify it (never resend) and tell the customer]
  -> [OTP challenge open and message is a code?  verify -> resume the held workflow]
  -> [pending action?  strict confirm / amend / cancel / await approval]
  -> REASON (intent + structured summary) -> intent-scoped tools
  -> loop (max MAX_AGENT_ITERATIONS):
        LLM proposes tool calls -> PLAN (DAG, templates, plan policy) -> POLICY (per step, Tool Gateway)
        -> ACT (parallel reads / serialized mutations) -> VERIFY (mutations) -> tool results back to the LLM
  -> RESPOND (LLM text grounded in tool results; deterministic text for holds and unconfirmed outcomes)
  -> grounding check -> persist assistant message -> save session (version-checked)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.agents.execution import ExecutionEngine
from app.agents.execution.executor import ExecutionContext, StepEvent
from app.agents.execution.models import (
    ACTIVE_WORKFLOW,
    ExecutionResult,
    ExecutionStep,
    StepKind,
    StepOrigin,
    StepStatus,
    WorkflowState,
    WorkflowStatus,
)
from app.agents.planner import Planner, TurnPlan
from app.agents.prompts import msg, system_prompt
from app.agents.state import PendingAction, SessionState
from app.agents.tool_selector import select_tools
from app.auth.authentication import CustomerAuthService
from app.config import Settings
from app.domain import (
    AgentRequest,
    AgentResponse,
    AuthState,
    Channel,
    HandoffReason,
    Intent,
    PolicyDecisionType,
    RuntimeEvent,
    SessionStatus,
    SourceCitation,
    ToolCallSummary,
    utcnow,
)
from app.escalation.handoff import HandoffService
from app.i18n import lexicon
from app.i18n.detector import LanguageDetector
from app.i18n.formatting import format_inr
from app.knowledge.citations import ungrounded_numbers, used_citations
from app.llm.base import LLMMessage, LLMProvider, LLMToolCall
from app.observability import metrics
from app.observability.tracing import span
from app.policies.approval import ActionGrant, ApprovalService
from app.security.audit import AuditLogger
from app.security.injection import injection_score
from app.security.pii import PIIDetector, PIIType, extract_code
from app.security.redaction import RedactionProfile, redact_data, strip_secrets
from app.sessions.manager import SessionBusy, SessionManager
from app.sessions.memory import ConversationMemory
from app.tools.registry import ToolRegistry
from app.tools.router import GatewayOutcome, ToolGateway
from app.tools.schemas import ToolCallRequest, ToolContext, ToolDefinition, ToolResult

log = logging.getLogger(__name__)
APPROVAL_HOLD_SECONDS = 3600
HOLD = (PolicyDecisionType.REQUIRE_AUTH, PolicyDecisionType.REQUIRE_CONFIRMATION, PolicyDecisionType.REQUIRE_HUMAN_APPROVAL)
# which hold to ask for first when several steps are held: authentication unlocks the others
_HOLD_PRIORITY = {PolicyDecisionType.REQUIRE_AUTH.value: 0, PolicyDecisionType.REQUIRE_HUMAN_APPROVAL.value: 1,
                  PolicyDecisionType.REQUIRE_CONFIRMATION.value: 2}
_SUMMARY_STATUS = {StepStatus.COMPLETED: "completed", StepStatus.HELD: "pending", StepStatus.REJECTED: "denied"}


@dataclass
class AgentProfile:
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    institution_type: str
    agent_id: str | None
    agent_name: str
    persona: str
    allowed_tools: list[str] | None
    voice_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Turn:
    text_parts: list[str] = field(default_factory=list)
    tool_summaries: list[ToolCallSummary] = field(default_factory=list)
    sources: list[SourceCitation] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    intent: Intent | None = None
    handoff_id: str | None = None
    stop: bool = False
    interrupted: bool = False
    error: str | None = None
    first_token_at: float | None = None
    pending_handoff: tuple[HandoffReason, str | None, str] | None = None  # started after the execution pass
    deferred: set[str] = field(default_factory=set)  # mutation steps whose result is reported after verification
    progress_spoken: bool = False

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()


def _delta(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="message.delta", content=text)


class Orchestrator:
    def __init__(self, *, settings: Settings, sessions: SessionManager, memory: ConversationMemory, llm: LLMProvider,
                 planner: Planner, registry: ToolRegistry, gateway: ToolGateway, auth: CustomerAuthService,
                 handoff: HandoffService, approvals: ApprovalService, audit: AuditLogger, profiles,
                 engine: ExecutionEngine | None = None) -> None:
        self.settings = settings
        self.sessions = sessions
        self.memory = memory
        self.llm = llm
        self.planner = planner
        self.registry = registry
        self.gateway = gateway
        self.auth = auth
        self.handoff = handoff
        self.approvals = approvals
        self.audit = audit
        self.profiles = profiles  # AgentDirectory
        self.engine = engine or ExecutionEngine(settings, gateway.db, gateway)
        self.detector = LanguageDetector()
        self.pii = PIIDetector()

    # ------------------------------------------------------------------------------------------
    async def run(self, req: AgentRequest) -> AsyncIterator[RuntimeEvent]:
        started = time.perf_counter()
        turn = Turn()
        ch = req.channel.value
        yield RuntimeEvent(type="processing.started", data={"request_id": req.request_id})
        with span("agent.turn", **{"channel": ch, "tenant.id": req.tenant_id, "session.id": req.session_id}):
            try:
                lock = self.sessions.locked(req.session_id, req.tenant_id)
                state = await lock.__aenter__()
            except SessionBusy:
                # another request (another tab, a voice turn, another worker) holds this session: never interleave
                text = msg("busy", "en")
                yield _delta(text)
                metrics.task_outcomes.labels(ch, "busy").inc()
                yield RuntimeEvent(type="message.completed", response=AgentResponse(
                    text=text, session_id=req.session_id, error="session_busy"))
                return
            try:
                try:
                    async for ev in self._turn(req, state, turn):
                        if ev.type == "message.delta":
                            if turn.first_token_at is None:
                                turn.first_token_at = time.perf_counter()
                                metrics.first_token_latency.labels(ch).observe(turn.first_token_at - started)
                        yield ev
                except (asyncio.CancelledError, GeneratorExit):
                    turn.interrupted = True
                    # a barge-in must not release the session while a money movement is still on the wire
                    await self.engine.inflight.drain(req.session_id, timeout=self.settings.default_tool_timeout + 2)
                    await self._persist_assistant(state, req, turn)
                    metrics.task_outcomes.labels(ch, "interrupted").inc()
                    raise
                except Exception:
                    log.exception("agent turn failed", extra={"session_id": req.session_id})
                    turn.error = "internal_error"
                    text = msg("error", state.response_language_tag)
                    turn.text_parts = [text]
                    yield _delta(text)
                response = await self._finalize(state, req, turn)
            except BaseException as e:
                await lock.__aexit__(type(e), e, e.__traceback__)
                raise
            else:
                await lock.__aexit__(None, None, None)
            metrics.turn_latency.labels(ch).observe(time.perf_counter() - started)
            metrics.task_outcomes.labels(ch, "error" if turn.error else "handoff" if response.handoff else
                                         "pending" if response.pending_action else "completed").inc()
        yield RuntimeEvent(type="message.completed", response=response)

    # ------------------------------------------------------------------------------------------
    async def _turn(self, req: AgentRequest, state: SessionState, turn: Turn) -> AsyncIterator[RuntimeEvent]:
        self._update_language(state, req)
        code = extract_code(req.message) if state.auth_challenge else None
        stored = "[OTP REDACTED]" if code else strip_secrets(req.message)
        if injection_score(req.message) >= 0.5:
            await self.audit.record(state.tenant_id, "security.prompt_injection_suspected", actor_type="customer",
                                    session_id=state.session_id, channel=req.channel.value, outcome="flagged")
        await self.memory.append(tenant_id=state.tenant_id, conversation_id=state.conversation_id, session_id=state.session_id,
                                 role="user", channel=req.channel.value, content=stored, language=state.language)

        if state.status == SessionStatus.HANDED_OFF:
            await self.handoff.relay_customer_message(state, stored)
            turn.handoff_id = state.handoff_id
            async for ev in self._say(turn, msg("relayed", state.response_language_tag)):
                yield ev
            return

        if state.pending_action and state.pending_action.workflow_id:
            await self._drop_stale_pending(state)

        if not state.pending_action and state.last_action_workflow_id and len(stored.split()) <= 6 \
                and lexicon.STOP.search(stored):
            # "Wait!" after an account change may already be at the bank: say exactly where it stands, never
            # attempt an unsafe cancellation/reversal because the voice response was interrupted
            async for ev in self._already_submitted(req, state, turn):
                yield ev
            if turn.stop:
                return

        if state.active_workflow_id:
            async for ev in self._reconcile(req, state, turn):
                yield ev
            if turn.stop:
                return

        if state.pending_action and state.pending_action.expired:
            await self._clear_pending(state, WorkflowStatus.EXPIRED)
            if lexicon.AFFIRM.search(stored) or code:
                async for ev in self._say(turn, msg("expired", state.response_language_tag)):
                    yield ev
                return

        if state.auth_challenge and code:
            async for ev in self._handle_otp(req, state, turn, code):
                yield ev
            return

        if state.pending_action:
            async for ev in self._handle_pending(req, state, turn, stored):
                yield ev
            if turn.stop:
                return

        history = await self.memory.history(state.tenant_id, state.conversation_id)
        recent_users = [m.content for m in history if m.role == "user"][:-1]
        plan = await self.planner.plan(stored, state, recent_users)
        turn.intent = plan.intent
        state.current_intent = plan.intent
        metrics.intents.labels(req.channel.value, plan.intent.value).inc()
        yield RuntimeEvent(type="intent.detected", data={"intent": plan.intent.value, "confidence": plan.confidence,
                                                         "language": state.language, "language_tag": state.response_language_tag,
                                                         "carried_over": plan.carried_over})

        if plan.intent == Intent.HUMAN_HANDOFF:
            async for ev in self._start_handoff(state, turn, HandoffReason.CUSTOMER_REQUEST, note=stored):
                yield ev
            return
        if plan.intent == Intent.FRAUD_REQUEST:
            state.fraud_flagged = True

        async for ev in self._agent_loop(req, state, turn, plan, history):
            yield ev

        if plan.intent == Intent.FRAUD_REQUEST and not turn.handoff_id and not state.pending_action:
            async for ev in self._start_handoff(state, turn, HandoffReason.FRAUD, note=stored, announce="fraud_handoff"):
                yield ev

    # ------------------------------------------------------------------------------------------
    async def _agent_loop(self, req: AgentRequest, state: SessionState, turn: Turn, plan: TurnPlan, history) -> AsyncIterator[RuntimeEvent]:
        profile = await self.profiles.get(state.tenant_id, state.agent_id)
        all_tools = await self.registry.tools_for(state.tenant_id)
        offered = select_tools(all_tools, intent=plan.intent, agent_allowlist=profile.allowed_tools)
        allowed = self._allowed(profile, all_tools)
        messages = await self._base_messages(state, req, plan.intent, history, profile)
        specs = [t.llm_spec() for t in offered.values()] or None
        # REASON: structured, user-safe understanding of the request (never model chain-of-thought)
        reasoning = self.engine.planner.reason(intent=plan.intent, entities=plan.entities, offered=offered)
        wf = self.engine.new_workflow(tenant_id=state.tenant_id, session_id=state.session_id,
                                      conversation_id=state.conversation_id,
                                      context={"intent": plan.intent.value, "language": state.language,
                                               "reasoning": reasoning.model_dump(mode="json"),
                                               "offered": sorted(offered)})  # resume re-offers exactly these

        for _ in range(self.settings.max_agent_iterations):
            resp = None
            async for ev in self.llm.stream(messages, tools=specs, channel=req.channel.value):
                if ev.type == "delta" and ev.delta:
                    turn.text_parts.append(ev.delta)
                    yield _delta(ev.delta)
                elif ev.type == "done":
                    resp = ev.response
            if resp is None or not resp.tool_calls:
                return  # RESPOND: the model answered from the tool results it has
            calls = list(resp.tool_calls)
            messages.append(LLMMessage(role="assistant", content=resp.content or None, tool_calls=calls))
            # PLAN (+ plan-level policy): the model's calls become a validated DAG; it cannot order or parallelise them
            batch, aliases = self.engine.planner.extend(wf, calls, offered=offered, all_tools=all_tools)
            if any(st.mutates and st.status == StepStatus.PENDING for st in batch):
                yield RuntimeEvent(type="workflow.progress", content=msg("ack_preparing", state.response_language_tag),
                                   data={"workflow_id": wf.workflow_id, "phase": "preparing"})
            for st in batch:
                if st.status == StepStatus.REJECTED:
                    async for ev in self._step_event(req, state, turn, wf, StepEvent(st)):
                        yield ev
            # POLICY (per step, in the gateway) + ACT + VERIFY
            result = None
            async for ev in self._run_workflow(req, state, turn, wf, offered=offered, all_tools=all_tools, allowed=allowed):
                if isinstance(ev, ExecutionResult):
                    result = ev
                else:
                    yield ev
            if turn.stop or result is None:
                return
            for call in calls:
                step = wf.step(aliases.get(call.id, "")) or next((x for x in wf.steps if x.call_id == call.id), None)
                content = result.tool_message(step) if step else ToolResult(ok=False, error="not executed").to_llm_content()
                messages.append(LLMMessage(role="tool", tool_call_id=call.id, name=call.name, content=content))
        async for ev in self._say(turn, " " + msg("error", state.response_language_tag)):
            yield ev

    # ------------------------------------------------------------------------------------------ execution glue
    async def _run_workflow(self, req: AgentRequest, state: SessionState, turn: Turn, wf: WorkflowState, *,
                            offered: dict[str, ToolDefinition], all_tools: dict[str, ToolDefinition], allowed: set[str] | None,
                            grants: dict[str, ActionGrant] | None = None, intent: Intent | None = None) -> AsyncIterator[Any]:
        """One execution pass over the workflow; then holds, unconfirmed outcomes and handoffs, in that order."""
        lang = state.response_language_tag
        before = {x.id: x.status for x in wf.steps}
        ec = ExecutionContext(tool_ctx=lambda: self._ctx(state, req, intent=intent), offered=offered, all_tools=all_tools,
                              allowed=allowed, deadline=time.monotonic() + self.settings.workflow_timeout, grants=grants or {})
        if wf.has_mutation:
            wf.persistent = True  # durable before anything is sent: survives a worker restart
            # set before executing: if this turn is cancelled (barge-in, hangup) the next one still finds it
            state.active_workflow_id = state.last_action_workflow_id = wf.workflow_id
        result: ExecutionResult | None = None
        async for item in self.engine.executor.stream(wf, ec):
            if isinstance(item, ExecutionResult):
                result = item
            elif isinstance(item, StepEvent):
                async for ev in self._step_event(req, state, turn, wf, item, outcome=item.outcome):
                    yield ev
            elif item.type == "workflow.progress":
                # neutral acknowledgements only: none of them implies the action succeeded
                phase = item.data.get("phase")
                text = None
                if phase == "submitting" and not turn.progress_spoken:  # policy allowed it; it is going to the bank
                    turn.progress_spoken = True
                    text = msg("ack_mutation", lang)
                elif phase == "slow":
                    text = msg("ack_slow_mutation" if item.data.get("mutation") else "ack_slow", lang)
                elif phase == "verifying":
                    vstep = wf.step(item.data.get("step_id", ""))
                    parent = wf.step(vstep.verifies or "") if vstep else None
                    if parent is not None and parent.status == StepStatus.UNKNOWN:  # only worth saying when ambiguous
                        text = msg("ack_verifying", lang)
                yield RuntimeEvent(type="workflow.progress", content=text, data=item.data)
            else:
                yield item
        assert result is not None
        for sid in sorted(turn.deferred):  # verification never ran (e.g. workflow deadline): report what we know
            turn.deferred.discard(sid)
            if (pending := wf.step(sid)) is not None:
                async for ev in self._report_step(req, state, turn, wf, pending):
                    yield ev
        changed = [x for x in wf.steps if before.get(x.id) != x.status]

        # holds: deterministic prompts, one at a time (authentication first: it unlocks the rest)
        held = sorted((x for x in wf.steps if x.status == StepStatus.HELD and x.id in ec.outcomes),
                      key=lambda x: (_HOLD_PRIORITY.get(x.hold_decision or "", 9), wf.steps.index(x)))
        if held:
            done_now = [x for x in changed if x.origin == StepOrigin.LLM and x.status == StepStatus.COMPLETED and x.call_id]
            if done_now and not turn.text_parts:  # answer what we already have (e.g. the balance) before asking
                async for ev in self._compose(req, state, turn, done_now, result, intent=intent):
                    yield ev
            outcome = ec.outcomes[held[0].id]
            assert outcome.tool is not None
            wf.persistent = True
            async for ev in self._hold(req, state, turn, outcome.tool, outcome, wf=wf, step=held[0]):
                yield ev
            await self.engine.store.save(wf)
            turn.stop = True
            yield result
            return

        # mutations whose outcome is not a verified success: deterministic wording, never model text
        changed_ids = {x.id for x in changed}
        for m in wf.mutation_steps():
            # report when the mutation itself changed, or when it was (re-)verified in this pass
            if m.id not in changed_ids and not any(x.verifies == m.id and x.id in changed_ids for x in wf.steps):
                continue
            v = m.verification
            summary = _action_summary(all_tools[m.tool], m.all_arguments) if m.tool in all_tools else (m.tool or "")
            ref = f", reference {v.reference}" if v and v.reference else ""
            if m.status == StepStatus.SUBMITTING:  # sent (or about to be) but no outcome recorded: treat as unknown
                m.status, m.status_reason = StepStatus.UNKNOWN, "outcome not recorded"
                wf.status = WorkflowStatus.ESCALATED
            if m.status == StepStatus.UNKNOWN:
                if v and v.detail == "still processing":
                    text, reason = msg("mutation_processing", lang, summary=summary, ref=ref), None
                else:
                    text, reason = msg("mutation_unknown", lang, summary=summary), HandoffReason.HIGH_RISK_OPERATION
                    wf.status = WorkflowStatus.ESCALATED
                state.active_workflow_id = wf.workflow_id
                wf.reported = True
                await self.engine.store.save(wf)
                async for ev in self._say(turn, (" " if turn.text_parts else "") + text):
                    yield ev
                if reason:
                    async for ev in self._start_handoff(state, turn, reason, note=f"Unconfirmed outcome: {summary} "
                                                        f"(workflow {wf.workflow_id}, key {m.idempotency_key})"):
                        yield ev
                turn.stop = True
                yield result
                return
            if m.status == StepStatus.FAILED and v and v.method == "status_lookup":
                async for ev in self._say(turn, (" " if turn.text_parts else "") + msg("mutation_not_processed", lang, summary=summary)):
                    yield ev
                wf.reported = True
                await self.engine.store.save(wf)
                turn.stop = True
        if state.active_workflow_id == wf.workflow_id and wf.status not in ACTIVE_WORKFLOW and wf.reported:
            state.active_workflow_id = None

        if turn.pending_handoff and not turn.handoff_id:
            reason, note, announce = turn.pending_handoff
            async for ev in self._start_handoff(state, turn, reason, note=note, announce=announce):
                yield ev
        yield result

    async def _compose(self, req: AgentRequest, state: SessionState, turn: Turn, steps: list[ExecutionStep],
                       result: ExecutionResult, intent: Intent | None) -> AsyncIterator[RuntimeEvent]:
        """RESPOND for already-executed steps (resume / partial results): the model words it from tool results only."""
        profile = await self.profiles.get(state.tenant_id, state.agent_id)
        history = await self.memory.history(state.tenant_id, state.conversation_id)
        messages = await self._base_messages(state, req, intent or state.current_intent, history, profile)
        calls = [LLMToolCall(id=x.call_id or x.id, name=x.tool or "", arguments=x.arguments) for x in steps]
        messages.append(LLMMessage(role="assistant", tool_calls=calls))
        for x, c in zip(steps, calls, strict=True):
            messages.append(LLMMessage(role="tool", tool_call_id=c.id, name=c.name, content=result.tool_message(x)))
        async for ev in self.llm.stream(messages, tools=None, channel=req.channel.value):
            if ev.type == "delta" and ev.delta:
                turn.text_parts.append(ev.delta)
                yield _delta(ev.delta)

    async def _step_event(self, req: AgentRequest, state: SessionState, turn: Turn, wf: WorkflowState, ev: StepEvent,
                          outcome: GatewayOutcome | None = None) -> AsyncIterator[RuntimeEvent]:
        """Per-step bookkeeping and events (summaries, counters, sources). Holds/handoffs are handled after the pass."""
        st = ev.step
        if st.kind == StepKind.CHECK:
            return
        r = st.result
        if st.kind == StepKind.VERIFY:
            parent = wf.step(st.verifies or "")
            v = st.verification
            yield RuntimeEvent(type="verification.completed", tool=parent.tool if parent else None, data={
                "workflow_id": wf.workflow_id, "step_id": parent.id if parent else None,
                "status": v.status.value if v else None, "method": v.method if v else None,
                "reference": v.reference if v else None})
            if parent is not None and parent.id in turn.deferred:
                turn.deferred.discard(parent.id)
                async for e in self._report_step(req, state, turn, wf, parent):
                    yield e
            return
        decision = outcome.decision if outcome else None
        status = _SUMMARY_STATUS.get(st.status) or ("denied" if r and r.policy_decision == "DENY" else "failed")
        turn.tool_summaries.append(ToolCallSummary(
            id=st.call_id or f"{wf.workflow_id[:8]}:{st.id}", name=st.tool or "", status=status,
            decision=decision.decision if decision else None, latency_ms=r.latency_ms if r else None,
            arguments=redact_data(outcome.arguments if outcome else st.arguments, RedactionProfile.LOG)))
        if st.status == StepStatus.HELD:
            return
        if st.mutates and st.status in (StepStatus.COMPLETED, StepStatus.UNKNOWN) and any(
                x.verifies == st.id and x.status == StepStatus.PENDING for x in wf.steps):
            turn.deferred.add(st.id)  # success is reported only once verified
            if st.tool == "transfer_money" and isinstance(st.all_arguments.get("amount"), int | float):
                state.transfer_total_today += float(st.all_arguments["amount"])  # counts towards limits even if unconfirmed
                state.transfer_count_today += 1
            return
        if st.mutates and st.tool == "transfer_money" and st.status in (StepStatus.COMPLETED, StepStatus.UNKNOWN) \
                and isinstance(st.all_arguments.get("amount"), int | float):
            state.transfer_total_today += float(st.all_arguments["amount"])
            state.transfer_count_today += 1
        async for e in self._report_step(req, state, turn, wf, st):
            yield e

    async def _report_step(self, req: AgentRequest, state: SessionState, turn: Turn, wf: WorkflowState,
                           st: ExecutionStep) -> AsyncIterator[RuntimeEvent]:
        r = st.result
        if st.mutates and st.status in (StepStatus.COMPLETED, StepStatus.FAILED):
            wf.reported = True  # the outcome reaches the customer in this turn (saved with the workflow)
        name = st.tool or ""
        tool = (await self.registry.tools_for(state.tenant_id)).get(name)
        base = {"step_id": st.id, "workflow_id": wf.workflow_id, "parallel_group": st.parallel_group,
                "latency_ms": r.latency_ms if r else None, "source": tool.source.value if tool else None,
                "verification": st.verification.status.value if st.verification else None}
        if st.status != StepStatus.COMPLETED or not r or not r.ok:
            if st.status == StepStatus.REJECTED or (r and r.policy_decision == "DENY"):
                if st.status != StepStatus.REJECTED:
                    state.policy_rejections += 1
            elif st.status not in (StepStatus.SKIPPED, StepStatus.CANCELLED):
                state.consecutive_failures += 1
            yield RuntimeEvent(type="tool.failed", tool=name, data={
                **base, "error": (r.error if r else None) or st.status_reason, "policy_decision": r.policy_decision if r else None,
                "outcome": (r.failure_kind if r else None) or ("not_executed" if st.status in (StepStatus.SKIPPED, StepStatus.REJECTED,
                                                                                             StepStatus.CANCELLED) else "unknown"),
                "category": r.failure_category if r else None, "status": st.status.value})
            if state.policy_rejections >= 2 and not turn.pending_handoff:
                turn.pending_handoff = (HandoffReason.POLICY_REJECTION, None, "handoff")
            elif state.consecutive_failures >= 3 and not turn.pending_handoff:
                turn.pending_handoff = (HandoffReason.REPEATED_FAILURE, None, "handoff")
            return
        state.consecutive_failures = 0
        data = r.data
        turn.evidence.append(json.dumps({"ok": True, "data": data}, default=str, ensure_ascii=False))
        state.remember_tool(name, True, _compact(data))
        yield RuntimeEvent(type="tool.completed", tool=name, data={
            **base, "risk_level": tool.risk_level.value if tool else None,
            "result": data})  # already masked by the gateway (AUDIT profile)
        if name == "search_knowledge":
            sources = [SourceCitation.model_validate(x) for x in (data or {}).get("results", [])]
            turn.sources = sources
            state.rag_context = sources
            yield RuntimeEvent(type="knowledge.sources", data={"sources": [x.model_dump() for x in sources]})
        elif name == "request_human_handoff" and not turn.pending_handoff:
            reason = _handoff_reason(st.arguments.get("reason"))
            turn.pending_handoff = (reason, st.arguments.get("note"), "fraud_handoff" if reason == HandoffReason.FRAUD else "handoff")

    async def _reconcile(self, req: AgentRequest, state: SessionState, turn: Turn, *, announce: bool = True) -> AsyncIterator[RuntimeEvent]:
        """A mutation from an earlier turn has no confirmed outcome (worker crash, timeout, bank still processing).
        Look it up — never resend it — and tell the customer before handling the new message."""
        wf = await self.engine.store.get(state.tenant_id, state.active_workflow_id or "")
        if wf is None or wf.session_id != state.session_id:
            state.active_workflow_id = None
            return
        await self.engine.inflight.drain(state.session_id, timeout=self.settings.default_tool_timeout + 2)
        wf = await self.engine.store.get(state.tenant_id, wf.workflow_id) or wf  # may have been updated by the drain
        await self._drop_stale_pending(state, wf)
        pa = state.pending_action
        stale = [m for m in wf.mutation_steps() if m.status in (StepStatus.SUBMITTING, StepStatus.UNKNOWN)]
        if not stale:
            if not wf.reported:  # the outcome was recorded but the customer never heard it (barge-in / hangup)
                all_tools = await self.registry.tools_for(state.tenant_id)
                for m in wf.mutation_steps():
                    if m.status not in (StepStatus.COMPLETED, StepStatus.FAILED):
                        continue
                    summary = _action_summary(all_tools[m.tool], m.all_arguments) if m.tool in all_tools else (m.tool or "")
                    if announce and m.status == StepStatus.COMPLETED:
                        ref = (m.verification.reference if m.verification else None) or (
                            (m.result.data or {}).get("transaction_ref") if m.result and isinstance(m.result.data, dict) else None)
                        text = msg("reconciled_success", state.response_language_tag, summary=summary,
                                   ref=f" (reference {ref})" if ref else "")
                    elif announce:
                        text = msg("reconciled_failed", state.response_language_tag, summary=summary,
                                   reason=(m.status_reason or (m.result.error if m.result else None) or "declined by the bank"))
                    else:
                        continue
                    async for ev in self._say(turn, text + " "):
                        yield ev
                wf.reported = True
                await self.engine.store.save(wf)
            if not (pa and pa.workflow_id == wf.workflow_id) and wf.status not in ACTIVE_WORKFLOW:
                state.active_workflow_id = None
            return
        all_tools = await self.registry.tools_for(state.tenant_id)
        for x in wf.steps:  # only verification runs here; anything else still queued from that turn is abandoned
            if x.status in (StepStatus.PENDING, StepStatus.HELD) and x.kind != StepKind.VERIFY:
                x.status, x.status_reason = StepStatus.CANCELLED, "superseded by recovery"
        for m in stale:
            if m.status == StepStatus.SUBMITTING:
                m.status, m.status_reason = StepStatus.UNKNOWN, "interrupted before the outcome was recorded"
            for x in wf.steps:
                if x.verifies == m.id:
                    x.status = StepStatus.PENDING  # look again
            if not any(x.verifies == m.id for x in wf.steps):
                wf.steps.append(ExecutionStep(id=wf.next_step_id(), kind=StepKind.VERIFY, origin=StepOrigin.TEMPLATE,
                                              tool=None, verifies=m.id, depends_on=[m.id]))
        lang = state.response_language_tag
        async for item in self._run_workflow(req, state, turn, wf, offered={}, all_tools=all_tools, allowed=None):
            if isinstance(item, RuntimeEvent):
                yield item
        if turn.stop and not turn.handoff_id and all(
                m.status != StepStatus.UNKNOWN or (m.verification and m.verification.detail == "still processing") for m in stale):
            turn.stop = False  # the update was given; the customer's new message is still handled (bank still processing)
        if turn.stop:
            return
        for m in stale:
            summary = _action_summary(all_tools[m.tool], m.all_arguments) if m.tool in all_tools else (m.tool or "")
            v = m.verification
            if m.status == StepStatus.COMPLETED and announce:
                ref = f" (reference {v.reference})" if v and v.reference else ""
                async for ev in self._say(turn, msg("reconciled_success", lang, summary=summary, ref=ref) + " "):
                    yield ev
        wf.reported = True
        await self.engine.store.save(wf)
        unresolved = any(m.status in (StepStatus.UNKNOWN, StepStatus.SUBMITTING) for m in wf.mutation_steps())
        if wf.status not in ACTIVE_WORKFLOW and not unresolved:
            state.active_workflow_id = None

    async def _process_outcome(self, req: AgentRequest, state: SessionState, turn: Turn, outcome: GatewayOutcome) -> AsyncIterator[RuntimeEvent]:
        d = outcome.decision
        turn.tool_summaries.append(ToolCallSummary(
            id=outcome.call.id, name=outcome.call.name, status=outcome.status, decision=d.decision if d else None,
            latency_ms=outcome.result.latency_ms, arguments=redact_data(outcome.arguments, RedactionProfile.LOG)))
        if d and d.decision in HOLD and outcome.tool:
            async for ev in self._hold(req, state, turn, outcome.tool, outcome):
                yield ev
            turn.stop = True
            return
        name = outcome.call.name
        if not outcome.result.ok:
            if d and d.decision == PolicyDecisionType.DENY or outcome.result.policy_decision == "DENY":
                state.policy_rejections += 1
            else:
                state.consecutive_failures += 1
            yield RuntimeEvent(type="tool.failed", tool=name, data={
                "error": outcome.result.error, "policy_decision": outcome.result.policy_decision,
                "outcome": outcome.result.failure_kind or "unknown", "latency_ms": outcome.result.latency_ms,
                "source": outcome.tool.source.value if outcome.tool else None})
            if state.policy_rejections >= 2:
                async for ev in self._start_handoff(state, turn, HandoffReason.POLICY_REJECTION):
                    yield ev
            elif state.consecutive_failures >= 3:
                async for ev in self._start_handoff(state, turn, HandoffReason.REPEATED_FAILURE):
                    yield ev
            return
        state.consecutive_failures = 0
        data = outcome.result.data
        turn.evidence.append(outcome.result.to_llm_content())
        state.remember_tool(name, True, _compact(data))
        yield RuntimeEvent(type="tool.completed", tool=name, data={
            "latency_ms": outcome.result.latency_ms, "source": outcome.tool.source.value if outcome.tool else None,
            "risk_level": outcome.tool.risk_level.value if outcome.tool else None,
            "result": data})  # already masked by the gateway (AUDIT profile)
        if name == "search_knowledge":
            sources = [SourceCitation.model_validate(r) for r in (data or {}).get("results", [])]
            turn.sources = sources
            state.rag_context = sources
            yield RuntimeEvent(type="knowledge.sources", data={"sources": [s.model_dump() for s in sources]})
        elif name == "request_human_handoff":
            reason = _handoff_reason(outcome.arguments.get("reason"))
            async for ev in self._start_handoff(state, turn, reason, note=outcome.arguments.get("note"),
                                                announce="fraud_handoff" if reason == HandoffReason.FRAUD else "handoff"):
                yield ev
        elif name == "transfer_money" and isinstance(outcome.arguments.get("amount"), int | float):
            state.transfer_total_today += float(outcome.arguments["amount"])
            state.transfer_count_today += 1

    # ------------------------------------------------------------------------------------------
    async def _hold(self, req: AgentRequest, state: SessionState, turn: Turn, tool: ToolDefinition, outcome: GatewayOutcome,
                    wf: WorkflowState | None = None, step: ExecutionStep | None = None) -> AsyncIterator[RuntimeEvent]:
        """Freeze the action and ask for the next factor. Prompts are deterministic, never LLM-generated."""
        d = outcome.decision
        assert d is not None
        lang = state.response_language_tag
        prev = state.pending_action
        summary = _action_summary(tool, outcome.arguments)
        resolved = _resolved_payee(wf, step)
        if resolved:  # the customer confirms the beneficiary the bank resolved, not just the name they said
            summary = f"{summary} ({resolved})"
        details = redact_data(outcome.arguments, RedactionProfile.AUDIT)
        pa = PendingAction.create(
            self.settings.pending_action_ttl_seconds, tool=tool.name, arguments=outcome.arguments, action_hash=d.action_hash,
            decision=d.decision, reason=d.reason, risk_level=d.risk_level.value, summary=summary,
            required_auth_state=d.required_auth_state, intent=state.current_intent,
            approval_id=prev.approval_id if prev and prev.action_hash == d.action_hash else None,
            workflow_id=wf.workflow_id if wf else None, step_id=step.id if step else None,
            bound_params=sorted(step.bound_arguments) if step else [],
        )
        state.pending_action = pa
        if wf is not None:
            state.active_workflow_id = wf.workflow_id
            wf.deadline = pa.expires_at
        turn.intent = turn.intent or state.current_intent

        if d.decision == PolicyDecisionType.REQUIRE_AUTH:
            if not state.customer_id:
                async for ev in self._say(turn, msg("need_identification", lang)):
                    yield ev
                yield RuntimeEvent(type="auth.required", data={"step": "identify", "required_auth_state": d.required_auth_state})
                return
            async for ev in self._send_otp(state, turn, pa):
                yield ev
        elif d.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            async for ev in self._say(turn, msg("confirm", lang, summary=summary)):
                yield ev
            yield RuntimeEvent(type="confirmation.required", tool=tool.name,
                               data={"action_id": pa.id, "summary": summary, "risk_level": pa.risk_level, "details": details,
                                     "expires_at": pa.expires_at.isoformat()})
        else:
            pa.expires_at = utcnow() + timedelta(seconds=APPROVAL_HOLD_SECONDS)  # checkers need longer than an OTP window
            if wf is not None:
                wf.deadline = pa.expires_at
            if not pa.approval_id:
                ar = await self.approvals.request(tenant_id=state.tenant_id, session_id=state.session_id,
                                                  conversation_id=state.conversation_id, tool=tool.name,
                                                  arguments=outcome.arguments,
                                                  risk_level=d.risk_level.value, reason=d.reason, action_hash=d.action_hash)
                pa.approval_id = ar.id
                text = msg("approval_pending", lang, ref=ar.id[:8].upper())
            else:
                text = msg("approval_waiting", lang, ref=pa.approval_id[:8].upper())
            async for ev in self._say(turn, text):
                yield ev
            yield RuntimeEvent(type="approval.required", tool=tool.name, data={"approval_id": pa.approval_id, "reason": d.reason,
                                                                                "summary": summary, "details": details,
                                                                                "risk_level": pa.risk_level})

    async def _send_otp(self, state: SessionState, turn: Turn, pa: PendingAction) -> AsyncIterator[RuntimeEvent]:
        lang = state.response_language_tag
        txn = pa.required_auth_state == AuthState.TRANSACTION_AUTHENTICATED
        out = await self.auth.start_otp(state, purpose="transaction" if txn else "login",
                                        action_hash=pa.action_hash if txn else None, action_summary=pa.summary)
        if not out.success:
            await self._clear_pending(state)
            if out.locked:
                async for ev in self._start_handoff(state, turn, HandoffReason.AUTHENTICATION_FAILURE, announce="otp_locked"):
                    yield ev
            else:
                async for ev in self._say(turn, msg("otp_unavailable", lang)):
                    yield ev
            return
        dest = out.masked_destination or ""
        text = msg("otp_transaction", lang, dest=dest, summary=pa.summary) if txn else msg("otp_login", lang, dest=dest)
        async for ev in self._say(turn, text):
            yield ev
        yield RuntimeEvent(type="auth.required", tool=pa.tool, data={
            "step": "otp", "purpose": "transaction" if txn else "login", "destination": dest,
            "required_auth_state": pa.required_auth_state, "action_id": pa.id, "summary": pa.summary,
            "details": redact_data(pa.arguments, RedactionProfile.AUDIT), "risk_level": pa.risk_level})

    async def _handle_otp(self, req: AgentRequest, state: SessionState, turn: Turn, code: str) -> AsyncIterator[RuntimeEvent]:
        lang = state.response_language_tag
        out = await self.auth.verify_otp(state, code)
        turn.intent = state.current_intent
        if not out.success:
            if out.locked:
                await self._clear_pending(state)
                async for ev in self._start_handoff(state, turn, HandoffReason.AUTHENTICATION_FAILURE, announce="otp_locked"):
                    yield ev
            else:
                left = max(0, self.settings.max_auth_failures - state.auth_failures)
                async for ev in self._say(turn, msg("otp_failed", lang, left=left)):
                    yield ev
            return
        if not state.pending_action:
            async for ev in self._say(turn, msg("verified", lang)):
                yield ev
            return
        async for ev in self._execute_pending(req, state, turn, grant=None):
            yield ev

    async def _handle_pending(self, req: AgentRequest, state: SessionState, turn: Turn, text: str) -> AsyncIterator[RuntimeEvent]:
        pa = state.pending_action
        assert pa is not None
        lang = state.response_language_tag
        turn.intent = state.current_intent
        reply = lexicon.confirmation_reply(text)
        if reply == "negate":
            await self.audit.record(state.tenant_id, "action.cancelled", actor_type="customer", session_id=state.session_id,
                                    channel=req.channel.value, resource=pa.tool)
            await self._clear_pending(state)
            turn.stop = True
            async for ev in self._say(turn, msg("cancelled", lang)):
                yield ev
            return
        short = len(text.split()) <= 4
        if pa.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            if reply == "affirm":
                turn.stop = True
                await self.audit.record(state.tenant_id, "action.confirmed", actor_type="customer", session_id=state.session_id,
                                        channel=req.channel.value, resource=pa.tool, payload={"action_hash": pa.action_hash})
                async for ev in self._execute_pending(req, state, turn, grant=ActionGrant(
                        action_hash=pa.action_hash, confirmed=True, approval_id=pa.approval_id)):
                    yield ev
                return
            if reply == "unclear":  # a "yes" with extra words: never guess — ask again, keep the frozen action
                turn.stop = True
                async for ev in self._say(turn, msg("confirm_unclear", lang, summary=pa.summary)):
                    yield ev
                return
            if reply == "amend":  # "yes, but make it ₹50,000": the confirmed action is NOT executed
                await self.audit.record(state.tenant_id, "action.amended", actor_type="customer", session_id=state.session_id,
                                        channel=req.channel.value, resource=pa.tool, payload={"action_hash": pa.action_hash})
            await self._clear_pending(state)  # anything else is a new request; the frozen action is dropped
            return
        if pa.decision == PolicyDecisionType.REQUIRE_AUTH:
            if state.auth_challenge:
                if short and reply != "amend":
                    turn.stop = True
                    async for ev in self._say(turn, msg("otp_reminder", lang, dest=state.auth_challenge.masked_destination or "")):
                        yield ev
                else:
                    await self._clear_pending(state)
                return
            phone = next((m.value for m in self.pii.detect(text) if m.type == PIIType.PHONE), None)
            turn.stop = True
            if phone and (await self.auth.identify(state, phone=re.sub(r"\D", "", phone)[-10:])).success:
                async for ev in self._send_otp(state, turn, pa):
                    yield ev
            else:
                async for ev in self._say(turn, msg("need_identification", lang)):
                    yield ev
            return
        # REQUIRE_HUMAN_APPROVAL
        status = await self.approvals.status(state.tenant_id, pa.approval_id) if pa.approval_id else None
        if status == "approved":
            turn.stop = True
            async for ev in self._execute_pending(req, state, turn, grant=ActionGrant(action_hash=pa.action_hash, approval_id=pa.approval_id)):
                yield ev
        elif status in ("rejected", "expired", None):
            await self._clear_pending(state)
            turn.stop = True
            async for ev in self._say(turn, msg("approval_rejected", lang, ref=(pa.approval_id or "")[:8].upper())):
                yield ev
        elif (short or lexicon.AFFIRM.search(text)) and reply != "amend":
            turn.stop = True
            async for ev in self._say(turn, msg("approval_waiting", lang, ref=pa.approval_id[:8].upper())):
                yield ev

    async def _execute_pending(self, req: AgentRequest, state: SessionState, turn: Turn, grant: ActionGrant | None) -> AsyncIterator[RuntimeEvent]:
        """Resume the held workflow: the frozen step re-enters the gateway/policy engine with exactly its arguments
        (plus the customer's grant, bound to its action hash); dependants and verification follow."""
        pa = state.pending_action
        assert pa is not None
        turn.stop = True
        if pa.workflow_id is None:  # pending action created before workflows existed
            async for ev in self._execute_pending_legacy(req, state, turn, grant):
                yield ev
            return
        all_tools = await self.registry.tools_for(state.tenant_id)
        wf = await self.engine.store.get(state.tenant_id, pa.workflow_id)
        step = wf.step(pa.step_id or "") if wf else None
        if wf is None or wf.session_id != state.session_id or step is None or step.status != StepStatus.HELD \
                or step.tool not in all_tools or wf.status not in ACTIVE_WORKFLOW:
            await self._clear_pending(state)
            async for ev in self._say(turn, msg("error", state.response_language_tag)):
                yield ev
            return
        profile = await self.profiles.get(state.tenant_id, state.agent_id)
        # exactly what the model was offered when it proposed these steps (a not-offered call stays not-offered)
        offered = {n: all_tools[n] for n in wf.context.get("offered", []) if n in all_tools}
        for x in wf.steps:
            if x.status == StepStatus.HELD:
                x.status, x.hold_decision = StepStatus.PENDING, None
        grants = {step.id: grant} if grant else {}
        before = {x.id: x.status for x in wf.steps}
        result = None
        turn.stop = False  # set again below; _run_workflow sets it when it has already answered (hold / unconfirmed)
        async for item in self._run_workflow(req, state, turn, wf, offered=offered, all_tools=all_tools,
                                             allowed=self._allowed(profile, all_tools), grants=grants, intent=pa.intent):
            if isinstance(item, ExecutionResult):
                result = item
            else:
                yield item
        if step.status not in (StepStatus.HELD, StepStatus.PENDING):  # the frozen action was submitted (or failed)
            if state.txn_auth_action_hash == pa.action_hash:
                self.auth.consume_transaction_auth(state)  # single-use
            if state.pending_action is pa:
                state.pending_action = None
        if turn.stop or result is None:
            return
        turn.stop = True
        done = [x for x in wf.steps if x.origin == StepOrigin.LLM and x.call_id and before.get(x.id) != x.status
                and x.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED)]
        if done:
            async for ev in self._compose(req, state, turn, done, result, intent=pa.intent):
                yield ev

    async def _execute_pending_legacy(self, req: AgentRequest, state: SessionState, turn: Turn, grant: ActionGrant | None) -> AsyncIterator[RuntimeEvent]:
        """Single-step resume for pending actions created before the workflow engine (kept for rolling upgrades)."""
        pa = state.pending_action
        assert pa is not None
        profile = await self.profiles.get(state.tenant_id, state.agent_id)
        all_tools = await self.registry.tools_for(state.tenant_id)
        tool = all_tools.get(pa.tool)
        if tool is None:
            await self._clear_pending(state)
            async for ev in self._say(turn, msg("error", state.response_language_tag)):
                yield ev
            return
        ctx = self._ctx(state, req, intent=pa.intent)
        bound = {k: v for k, v in pa.arguments.items() if k in pa.bound_params}
        call = ToolCallRequest(id=f"call_{pa.id[:12]}", name=pa.tool,
                               arguments={k: v for k, v in pa.arguments.items() if k not in pa.bound_params})
        yield RuntimeEvent(type="tool.started", tool=pa.tool)
        outcome = await self.gateway.execute(call, ctx, offered={tool.name: tool}, allowed_tools=self._allowed(profile, all_tools),
                                             grant=grant, bound_args=bound)
        if outcome.decision and outcome.decision.decision in HOLD:
            async for ev in self._process_outcome(req, state, turn, outcome):
                yield ev
            return
        bound_txn = state.txn_auth_action_hash == pa.action_hash
        state.pending_action = None
        if bound_txn:
            self.auth.consume_transaction_auth(state)
        turn.stop = False
        async for ev in self._process_outcome(req, state, turn, outcome):
            yield ev
        if turn.stop:
            return
        turn.stop = True
        history = await self.memory.history(state.tenant_id, state.conversation_id)
        messages = await self._base_messages(state, req, pa.intent, history, profile)
        messages += [
            LLMMessage(role="assistant", tool_calls=[LLMToolCall(id=call.id, name=call.name, arguments=call.arguments)]),
            LLMMessage(role="tool", tool_call_id=call.id, name=call.name, content=outcome.result.to_llm_content()),
        ]
        async for ev in self.llm.stream(messages, tools=None, channel=req.channel.value):
            if ev.type == "delta" and ev.delta:
                turn.text_parts.append(ev.delta)
                yield _delta(ev.delta)

    async def _drop_stale_pending(self, state: SessionState, wf: WorkflowState | None = None) -> None:
        """A pending confirmation whose step already went to the bank (barge-in, worker crash) must never be
        usable again; the outcome is established by verification instead."""
        pa = state.pending_action
        if pa is None or not pa.workflow_id:
            return
        if wf is None or wf.workflow_id != pa.workflow_id:
            wf = await self.engine.store.get(state.tenant_id, pa.workflow_id)
        step = wf.step(pa.step_id or "") if wf else None
        if wf is not None and (step is None or step.status != StepStatus.HELD):
            state.pending_action, state.auth_challenge = None, None
            self.auth.consume_transaction_auth(state)

    async def _already_submitted(self, req: AgentRequest, state: SessionState, turn: Turn,
                                 window_seconds: float = 180.0) -> AsyncIterator[RuntimeEvent]:
        """Deterministic answer to "wait!"/"stop!" right after an account change was submitted."""
        wf = await self.engine.store.get(state.tenant_id, state.last_action_workflow_id or "")
        if wf is None or wf.session_id != state.session_id:
            return
        sent = [m for m in wf.mutation_steps() if m.submitted_at is not None
                and (utcnow() - m.submitted_at).total_seconds() <= window_seconds]
        if not sent:
            return
        if state.active_workflow_id == wf.workflow_id:  # establish the real outcome first (verify, never resend)
            async for ev in self._reconcile(req, state, turn, announce=False):
                yield ev
            if turn.stop:
                return
            wf = await self.engine.store.get(state.tenant_id, wf.workflow_id) or wf
            sent = [wf.step(m.id) or m for m in sent]
        m = sent[-1]
        all_tools = await self.registry.tools_for(state.tenant_id)
        summary = _action_summary(all_tools[m.tool], m.all_arguments) if m.tool in all_tools else (m.tool or "")
        lang = state.response_language_tag
        if m.status == StepStatus.COMPLETED:
            ref = m.verification.reference if m.verification and m.verification.reference else None
            text = msg("already_done", lang, summary=summary, ref=f" (reference {ref})" if ref else "")
        elif m.status == StepStatus.FAILED:
            text = msg("already_not_processed", lang, summary=summary)
        else:
            text = msg("already_sent_unknown", lang, summary=summary)
        await self.audit.record(state.tenant_id, "action.stop_requested_after_submission", actor_type="customer",
                                session_id=state.session_id, channel=req.channel.value, resource=m.tool,
                                payload={"workflow_id": wf.workflow_id, "status": m.status.value})
        turn.intent = state.current_intent
        turn.stop = True
        async for ev in self._say(turn, text):
            yield ev

    async def end_channel(self, session_id: str, tenant_id: str, *, channel: Channel, close: bool, reason: str) -> dict[str, Any]:
        """A channel ended (phone hangup, WebRTC disconnect). Channel-agnostic cleanup, decided here — not in LiveKit:

        * an action still waiting for OTP / confirmation / approval was never submitted -> safe to cancel
        * a submitted account change is NEVER cancelled or reversed: in-flight submissions are awaited and an
          unconfirmed outcome is verified now; if it stays unknown a human follow-up (handoff) is created
        * single-use transaction authentication and any OTP challenge are discarded
        * `close=True` (a phone call is its own session) closes the session unless a human follow-up is open
        """
        await self.engine.inflight.drain(session_id, timeout=self.settings.default_tool_timeout + 2)
        out: dict[str, Any] = {"pending_cancelled": None, "unconfirmed": False, "handoff_id": None, "closed": False}
        req = AgentRequest(tenant_id=tenant_id, session_id=session_id, channel=channel, message="")
        turn = Turn()
        async with self.sessions.locked(session_id, tenant_id, wait=self.settings.session_lock_wait) as state:
            await self._drop_stale_pending(state)
            pa = state.pending_action
            if pa is not None:
                out["pending_cancelled"] = pa.tool
                await self.audit.record(state.tenant_id, "action.cancelled", actor_type="system", session_id=session_id,
                                        channel=channel.value, resource=pa.tool, payload={"reason": reason})
                await self._clear_pending(state, WorkflowStatus.CANCELLED)
            state.auth_challenge = None
            self.auth.consume_transaction_auth(state)
            if state.active_workflow_id:
                async for _ in self._reconcile(req, state, turn, announce=False):
                    pass  # nobody is listening any more; outcomes are recorded and audited
            wf = await self.engine.store.get(tenant_id, state.active_workflow_id) if state.active_workflow_id else None
            out["unconfirmed"] = bool(wf and any(m.status in (StepStatus.UNKNOWN, StepStatus.SUBMITTING)
                                                 for m in wf.mutation_steps()))
            if out["unconfirmed"] and state.status != SessionStatus.HANDED_OFF:
                h = await self.handoff.initiate(state, HandoffReason.HIGH_RISK_OPERATION,
                                                f"Call ended with an unconfirmed account change (workflow {wf.workflow_id}); "
                                                "verify and call the customer back.")
                out["handoff_id"] = h.id
            state.active_channels = [c for c in state.active_channels if c != channel]
            if state.channel == channel and state.active_channels:
                state.channel = state.active_channels[-1]
            state.call_id = None
            state.voice_room = None if channel == Channel.VOICE else state.voice_room
            if close and state.status == SessionStatus.ACTIVE and not out["unconfirmed"]:
                state.status = SessionStatus.CLOSED
                out["closed"] = True
            out["handoff_id"] = out["handoff_id"] or (state.handoff_id if state.status == SessionStatus.HANDED_OFF else None)
        return out

    # ------------------------------------------------------------------------------------------
    async def _start_handoff(self, state: SessionState, turn: Turn, reason: HandoffReason, note: str | None = None,
                             announce: str = "handoff") -> AsyncIterator[RuntimeEvent]:
        h = await self.handoff.initiate(state, reason, note)
        turn.handoff_id = h.id
        turn.stop = True
        text = msg(announce, state.response_language_tag)
        async for ev in self._say(turn, (" " if turn.text_parts else "") + text):
            yield ev
        yield RuntimeEvent(type="handoff.initiated", data={"handoff_id": h.id, "reason": reason.value, "priority": h.priority})

    async def _say(self, turn: Turn, text: str) -> AsyncIterator[RuntimeEvent]:
        for piece in re.findall(r"\s*\S+", text):
            turn.text_parts.append(piece)
            yield _delta(piece)

    async def _clear_pending(self, state: SessionState, status: WorkflowStatus = WorkflowStatus.CANCELLED) -> None:
        pa = state.pending_action
        state.pending_action = None
        state.auth_challenge = None
        self.auth.consume_transaction_auth(state)
        if pa and pa.workflow_id:  # the held workflow can never resume now
            wf = await self.engine.store.get(state.tenant_id, pa.workflow_id)
            if wf is not None and wf.status in ACTIVE_WORKFLOW and not any(
                    m.status in (StepStatus.SUBMITTING, StepStatus.UNKNOWN) for m in wf.mutation_steps()):
                for x in wf.steps:
                    if x.status in (StepStatus.HELD, StepStatus.PENDING):
                        x.status, x.status_reason = StepStatus.CANCELLED, status.value.lower()
                wf.status = status
                await self.engine.store.save(wf)
                metrics.workflow_outcomes.labels(wf.workflow_type, status.value).inc()
            if state.active_workflow_id == pa.workflow_id and (wf is None or wf.status not in ACTIVE_WORKFLOW):
                state.active_workflow_id = None

    def _update_language(self, state: SessionState, req: AgentRequest) -> None:
        det = self.detector.detect(req.message, hint=req.language)
        trivial = len(req.message.split()) <= 2 and det.script == "Latn"
        if not trivial and det.confidence >= 0.5:
            state.language, state.language_script = det.code, det.script

    @staticmethod
    def _allowed(profile: AgentProfile, all_tools: dict[str, ToolDefinition]) -> set[str] | None:
        if profile.allowed_tools is None:
            return None
        return set(profile.allowed_tools) | {n for n, t in all_tools.items() if t.source.value == "builtin"}

    def _ctx(self, state: SessionState, req: AgentRequest, intent: Intent | None = None) -> ToolContext:
        return ToolContext(
            tenant_id=state.tenant_id, session_id=state.session_id, conversation_id=state.conversation_id,
            agent_id=state.agent_id, customer_id=state.customer_id, channel=req.channel, language=state.language,
            auth_state=state.authentication_state, auth_methods=list(state.auth_methods),
            txn_auth_action_hash=state.txn_auth_action_hash, intent=intent or state.current_intent,
            request_id=req.request_id, session_stats=state.session_stats(),
        )

    async def _base_messages(self, state: SessionState, req: AgentRequest, intent: Intent | None, history, profile: AgentProfile) -> list[LLMMessage]:
        summary = await self.memory.summary(state.tenant_id, state.conversation_id)
        out = [LLMMessage(role="system", content=system_prompt(
            tenant_name=profile.tenant_name, institution_type=profile.institution_type, agent_name=profile.agent_name,
            persona=profile.persona, state=state, intent=intent, modality=req.output_modality, summary=summary))]
        for m in history:
            if m.role == "user":
                out.append(LLMMessage(role="user", content=m.content))
            elif m.role == "assistant":
                out.append(LLMMessage(role="assistant", content=m.content))
            elif m.role == "human_agent":
                out.append(LLMMessage(role="assistant", content=f"[human agent] {m.content}"))
        return out

    # ------------------------------------------------------------------------------------------
    async def _persist_assistant(self, state: SessionState, req: AgentRequest, turn: Turn) -> str | None:
        if not turn.text and not turn.tool_summaries and not turn.interrupted:
            return None
        return await self.memory.append(
            tenant_id=state.tenant_id, conversation_id=state.conversation_id, session_id=state.session_id, role="assistant",
            channel=req.channel.value, content=turn.text, language=state.language,
            intent=turn.intent.value if turn.intent else None,
            tool_calls=[t.model_dump(mode="json") for t in turn.tool_summaries],
            sources=[s.model_dump() for s in used_citations(turn.text, turn.sources)] if turn.sources else [],
            interrupted=turn.interrupted)

    async def _finalize(self, state: SessionState, req: AgentRequest, turn: Turn) -> AgentResponse:
        text = turn.text
        if turn.evidence and text:
            pending_summary = [state.pending_action.summary] if state.pending_action else []
            bad = ungrounded_numbers(text, [*turn.evidence, req.message, *pending_summary])
            if bad:
                metrics.ungrounded_answers.labels(req.channel.value).inc()
                log.warning("answer contains numbers not present in evidence", extra={"count": len(bad)})
        message_id = await self._persist_assistant(state, req, turn)
        if turn.intent:
            state.current_intent = turn.intent
        count = await self.memory.count(state.tenant_id, state.conversation_id)
        if count > self.settings.history_window_messages and count % 10 == 0:
            await self.memory.set_summary(state.tenant_id, state.conversation_id, await self.handoff.summarize(state),
                                          intent=state.current_intent.value if state.current_intent else None)
        return AgentResponse(
            text=text, intent=turn.intent, language=state.language, tool_calls=turn.tool_summaries,
            sources=used_citations(text, turn.sources) if turn.sources else [],
            handoff=state.status == SessionStatus.HANDED_OFF, handoff_id=state.handoff_id,
            pending_action=state.pending_action.view() if state.pending_action else None,
            authentication_state=state.authentication_state, session_id=state.session_id,
            conversation_id=state.conversation_id, message_id=message_id, interrupted=turn.interrupted, error=turn.error,
        )


def _resolved_payee(wf: WorkflowState | None, step: ExecutionStep | None) -> str | None:
    if wf is None or step is None:
        return None
    for ref in step.bindings.values():
        src = wf.step(ref.split(".", 1)[0])
        if src is not None and src.output.get("beneficiary_name"):
            acct = src.output.get("account_masked")
            return f"{src.output['beneficiary_name']}{', account ' + acct if acct else ''}"
    return None


def _handoff_reason(value: Any) -> HandoffReason:
    try:
        return HandoffReason(str(value))
    except ValueError:
        return HandoffReason.CUSTOMER_REQUEST


def _action_summary(tool: ToolDefinition, args: dict[str, Any]) -> str:
    fmt = {k: v for k, v in args.items()}
    for k, v in args.items():
        if isinstance(v, int | float) and ("amount" in k or k in ("value", "sum")):
            fmt[f"{k}_inr"] = format_inr(v)
    if tool.confirmation_template:
        try:
            return tool.confirmation_template.format_map(_Default(fmt))
        except (ValueError, IndexError):
            pass
    pretty = ", ".join(f"{k.replace('_', ' ')}: {format_inr(v) if 'amount' in k and isinstance(v, int | float) else v}"
                       for k, v in args.items())
    return f"{tool.name.replace('_', ' ')} ({pretty})" if pretty else tool.name.replace("_", " ")


class _Default(dict):
    def __missing__(self, key: str) -> str:
        return "-"


def _compact(data: Any, limit: int = 400) -> dict[str, Any]:
    red = redact_data(data, RedactionProfile.AUDIT)
    raw = json.dumps(red, default=str, ensure_ascii=False)
    return {"excerpt": raw[:limit]} if len(raw) > limit else (red if isinstance(red, dict) else {"value": red})
