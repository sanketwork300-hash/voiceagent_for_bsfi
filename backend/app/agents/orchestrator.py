"""Turn orchestration. Channel-agnostic: chat and voice turns run through exactly this code.

One turn:
  lock session -> language detection -> secret stripping -> persist user message
  -> [handed off?  relay to human]
  -> [OTP challenge open and message is a code?  verify -> resume pending action]
  -> [pending action?  confirm / cancel / await approval]
  -> plan (intent) -> intent-scoped tools -> LLM <-> Tool Gateway loop (policy-gated)
  -> grounding check -> persist assistant message -> save session
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
from app.sessions.manager import SessionManager
from app.sessions.memory import ConversationMemory
from app.tools.registry import ToolRegistry
from app.tools.router import GatewayOutcome, ToolGateway
from app.tools.schemas import ToolCallRequest, ToolContext, ToolDefinition

log = logging.getLogger(__name__)
APPROVAL_HOLD_SECONDS = 3600
HOLD = (PolicyDecisionType.REQUIRE_AUTH, PolicyDecisionType.REQUIRE_CONFIRMATION, PolicyDecisionType.REQUIRE_HUMAN_APPROVAL)


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

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()


def _delta(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="message.delta", content=text)


class Orchestrator:
    def __init__(self, *, settings: Settings, sessions: SessionManager, memory: ConversationMemory, llm: LLMProvider,
                 planner: Planner, registry: ToolRegistry, gateway: ToolGateway, auth: CustomerAuthService,
                 handoff: HandoffService, approvals: ApprovalService, audit: AuditLogger, profiles) -> None:
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
        self.detector = LanguageDetector()
        self.pii = PIIDetector()

    # ------------------------------------------------------------------------------------------
    async def run(self, req: AgentRequest) -> AsyncIterator[RuntimeEvent]:
        started = time.perf_counter()
        turn = Turn()
        ch = req.channel.value
        yield RuntimeEvent(type="processing.started", data={"request_id": req.request_id})
        with span("agent.turn", **{"channel": ch, "tenant.id": req.tenant_id, "session.id": req.session_id}):
            async with self.sessions.locked(req.session_id, req.tenant_id) as state:
                try:
                    async for ev in self._turn(req, state, turn):
                        if ev.type == "message.delta":
                            if turn.first_token_at is None:
                                turn.first_token_at = time.perf_counter()
                                metrics.first_token_latency.labels(ch).observe(turn.first_token_at - started)
                        yield ev
                except (asyncio.CancelledError, GeneratorExit):
                    turn.interrupted = True
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

        if state.pending_action and state.pending_action.expired:
            self._clear_pending(state)
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
        ctx = self._ctx(state, req)
        messages = await self._base_messages(state, req, plan.intent, history, profile)
        specs = [t.llm_spec() for t in offered.values()] or None

        for _ in range(self.settings.llm_max_tool_iterations):
            resp = None
            async for ev in self.llm.stream(messages, tools=specs, channel=req.channel.value):
                if ev.type == "delta" and ev.delta:
                    turn.text_parts.append(ev.delta)
                    yield _delta(ev.delta)
                elif ev.type == "done":
                    resp = ev.response
            if resp is None or not resp.tool_calls:
                return
            call = resp.tool_calls[0]  # one action at a time keeps policy holds unambiguous
            messages.append(LLMMessage(role="assistant", content=resp.content or None, tool_calls=[call]))
            yield RuntimeEvent(type="tool.started", tool=call.name)
            outcome = await self.gateway.execute(ToolCallRequest(id=call.id, name=call.name, arguments=call.arguments),
                                                 ctx, offered=offered, allowed_tools=allowed)
            async for ev in self._process_outcome(req, state, turn, outcome):
                yield ev
            if turn.stop:
                return
            messages.append(LLMMessage(role="tool", tool_call_id=call.id, name=call.name, content=outcome.result.to_llm_content()))
            ctx = self._ctx(state, req)  # state (e.g. stats) may have changed
        async for ev in self._say(turn, " " + msg("error", state.response_language_tag)):
            yield ev

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
    async def _hold(self, req: AgentRequest, state: SessionState, turn: Turn, tool: ToolDefinition, outcome: GatewayOutcome) -> AsyncIterator[RuntimeEvent]:
        """Freeze the action and ask for the next factor. Prompts are deterministic, never LLM-generated."""
        d = outcome.decision
        assert d is not None
        lang = state.response_language_tag
        prev = state.pending_action
        summary = _action_summary(tool, outcome.arguments)
        details = redact_data(outcome.arguments, RedactionProfile.AUDIT)
        pa = PendingAction.create(
            self.settings.pending_action_ttl_seconds, tool=tool.name, arguments=outcome.arguments, action_hash=d.action_hash,
            decision=d.decision, reason=d.reason, risk_level=d.risk_level.value, summary=summary,
            required_auth_state=d.required_auth_state, intent=state.current_intent,
            approval_id=prev.approval_id if prev and prev.action_hash == d.action_hash else None,
        )
        state.pending_action = pa
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
            if not pa.approval_id:
                ar = await self.approvals.request(tenant_id=state.tenant_id, session_id=state.session_id,
                                                  conversation_id=state.conversation_id, tool=tool.name,
                                                  arguments=outcome.arguments,
                                                  risk_level=d.risk_level.value, reason=d.reason)
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
            self._clear_pending(state)
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
                self._clear_pending(state)
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
        if lexicon.NEGATE.search(text):
            await self.audit.record(state.tenant_id, "action.cancelled", actor_type="customer", session_id=state.session_id,
                                    channel=req.channel.value, resource=pa.tool)
            self._clear_pending(state)
            turn.stop = True
            async for ev in self._say(turn, msg("cancelled", lang)):
                yield ev
            return
        short = len(text.split()) <= 4
        if pa.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            if lexicon.AFFIRM.search(text):
                turn.stop = True
                await self.audit.record(state.tenant_id, "action.confirmed", actor_type="customer", session_id=state.session_id,
                                        channel=req.channel.value, resource=pa.tool, payload={"action_hash": pa.action_hash})
                async for ev in self._execute_pending(req, state, turn, grant=ActionGrant(
                        action_hash=pa.action_hash, confirmed=True, approval_id=pa.approval_id)):
                    yield ev
                return
            self._clear_pending(state)  # anything else is a new request; the frozen action is dropped
            return
        if pa.decision == PolicyDecisionType.REQUIRE_AUTH:
            if state.auth_challenge:
                if short:
                    turn.stop = True
                    async for ev in self._say(turn, msg("otp_reminder", lang, dest=state.auth_challenge.masked_destination or "")):
                        yield ev
                else:
                    self._clear_pending(state)
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
            self._clear_pending(state)
            turn.stop = True
            async for ev in self._say(turn, msg("approval_rejected", lang, ref=(pa.approval_id or "")[:8].upper())):
                yield ev
        elif short or lexicon.AFFIRM.search(text):
            turn.stop = True
            async for ev in self._say(turn, msg("approval_waiting", lang, ref=pa.approval_id[:8].upper())):
                yield ev

    async def _execute_pending(self, req: AgentRequest, state: SessionState, turn: Turn, grant: ActionGrant | None) -> AsyncIterator[RuntimeEvent]:
        """Re-submit the frozen action (exact same arguments) to the gateway/policy engine."""
        pa = state.pending_action
        assert pa is not None
        turn.stop = True
        profile = await self.profiles.get(state.tenant_id, state.agent_id)
        all_tools = await self.registry.tools_for(state.tenant_id)
        tool = all_tools.get(pa.tool)
        if tool is None:
            self._clear_pending(state)
            async for ev in self._say(turn, msg("error", state.response_language_tag)):
                yield ev
            return
        ctx = self._ctx(state, req, intent=pa.intent)
        call = ToolCallRequest(id=f"call_{pa.id[:12]}", name=pa.tool, arguments=pa.arguments)
        yield RuntimeEvent(type="tool.started", tool=pa.tool)
        outcome = await self.gateway.execute(call, ctx, offered={tool.name: tool}, allowed_tools=self._allowed(profile, all_tools), grant=grant)
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

    def _clear_pending(self, state: SessionState) -> None:
        state.pending_action = None
        state.auth_challenge = None
        self.auth.consume_transaction_auth(state)

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
