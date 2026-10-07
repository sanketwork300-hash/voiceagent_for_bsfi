"""Policy Engine: the only component that can authorise a tool execution.

Flow for every tool call (identical for chat and voice):

    tool request -> enabled/allow-listed? -> tenant DENY rules -> authentication (state + factor strength)
                 -> contextual risk check / human approval -> explicit confirmation -> ALLOW

The LLM's output is an *input* to this engine; nothing the LLM says can change the authentication
state, the confirmation grant or the approval status it evaluates.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database.models import Policy
from app.database.session import Database
from app.domain import AuthMethod, AuthState, PolicyDecisionType, RiskLevel
from app.policies.approval import ActionGrant, ApprovalService, action_hash
from app.policies.risk import RiskScorer
from app.policies.rules import DEFAULT_RULES, PolicyRule, rule_matches
from app.tools.schemas import ToolContext, ToolDefinition

STRONG_FACTORS = {AuthMethod.CUSTOMER_ASSERTION.value, AuthMethod.OTP.value, AuthMethod.TRANSACTION_OTP.value}
RISK_AUTH_FLOOR = {
    RiskLevel.LOW: AuthState.UNAUTHENTICATED,
    RiskLevel.MEDIUM: AuthState.PARTIALLY_AUTHENTICATED,
    RiskLevel.HIGH: AuthState.FULLY_AUTHENTICATED,
    RiskLevel.CRITICAL: AuthState.TRANSACTION_AUTHENTICATED,
}


class PolicyDecision(BaseModel):
    decision: PolicyDecisionType
    reason: str
    risk_level: RiskLevel
    risk_score: float
    risk_factors: list[str] = Field(default_factory=list)
    required_auth_state: AuthState | None = None
    requires_strong_factor: bool = False
    matched_rules: list[str] = Field(default_factory=list)
    action_hash: str

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecisionType.ALLOW


def _max_auth(*states: AuthState) -> AuthState:
    return max(states, key=lambda s: s.level)


class PolicyEngine:
    def __init__(self, db: Database, approvals: ApprovalService, scorer: RiskScorer | None = None,
                 cache_ttl_seconds: float = 30.0) -> None:
        self.db = db
        self.approvals = approvals
        self.scorer = scorer or RiskScorer()
        self._cache: dict[str, tuple[float, list[PolicyRule]]] = {}
        self._ttl = cache_ttl_seconds

    def invalidate(self, tenant_id: str) -> None:
        self._cache.pop(tenant_id, None)

    async def rules_for(self, tenant_id: str) -> list[PolicyRule]:
        cached = self._cache.get(tenant_id)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        async with self.db.session() as s:
            rows = (await s.execute(select(Policy).where(Policy.tenant_id == tenant_id))).scalars().all()
        by_id = {r.id: r for r in DEFAULT_RULES}
        for row in rows:
            by_id[row.id] = PolicyRule(id=row.id, name=row.name, priority=row.priority, conditions=row.conditions,
                                       effect=row.effect, params=row.params, enabled=row.is_enabled)
        rules = sorted((r for r in by_id.values() if r.enabled), key=lambda r: r.priority)
        self._cache[tenant_id] = (time.monotonic(), rules)
        return rules

    async def evaluate(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext, *,
                       allowed_tools: set[str] | None = None, grant: ActionGrant | None = None) -> PolicyDecision:
        ahash = action_hash(tool.name, args)
        assessment = self.scorer.assess(tool=tool.name, base=tool.risk_level, args=args, channel=ctx.channel.value,
                                        intent=ctx.intent, session=ctx.session_stats)
        risk = assessment.level

        def decide(d: PolicyDecisionType, reason: str, **kw: Any) -> PolicyDecision:
            return PolicyDecision(decision=d, reason=reason, risk_level=risk, risk_score=assessment.score,
                                  risk_factors=assessment.factors, action_hash=ahash, matched_rules=matched, **kw)

        matched: list[str] = []
        if not tool.enabled:
            return decide(PolicyDecisionType.DENY, "Tool is disabled for this institution.")
        if allowed_tools is not None and tool.name not in allowed_tools:
            return decide(PolicyDecisionType.DENY, "Tool is not permitted for this agent.")
        if tool.internal:
            return decide(PolicyDecisionType.DENY, "Internal tools cannot be invoked by the assistant.")

        rules = [r for r in await self.rules_for(ctx.tenant_id) if rule_matches(
            r, tool=tool.name, source=tool.source.value, risk=risk, intent=ctx.intent.value if ctx.intent else None,
            channel=ctx.channel.value, auth_state=ctx.auth_state, args=args, session=ctx.session_stats)]
        matched = [r.id for r in rules]

        # 1. hard denials
        for r in rules:
            if r.effect == "DENY":
                return decide(PolicyDecisionType.DENY, r.params.get("reason", r.name))

        # 2. authentication: required level & factor strength
        min_auth, relaxed = tool.min_auth_state, False
        for r in rules:
            if r.effect == "SET_MIN_AUTH":
                min_auth, relaxed = AuthState(r.params["min_auth_state"]), True
        # The risk floor protects against mis-configured tools; an explicit relax rule (e.g. protective card
        # block during fraud) may lower it, but only while context hasn't raised the risk above the tool's base.
        if relaxed and risk.level <= tool.risk_level.level:
            required = min_auth
        else:
            required = _max_auth(min_auth, RISK_AUTH_FLOOR[risk])
        for r in rules:
            if r.effect == "REQUIRE_AUTH":
                required = _max_auth(required, AuthState(r.params.get("required_auth_state", AuthState.FULLY_AUTHENTICATED)))

        effective = ctx.auth_state
        if effective == AuthState.TRANSACTION_AUTHENTICATED and ctx.txn_auth_action_hash != ahash:
            effective = AuthState.FULLY_AUTHENTICATED  # transaction auth is single-use and bound to one action
        strong = bool(STRONG_FACTORS & set(ctx.auth_methods))
        if risk.level >= RiskLevel.HIGH.level and not strong and effective.level > AuthState.PARTIALLY_AUTHENTICATED.level:
            effective = AuthState.PARTIALLY_AUTHENTICATED  # e.g. voice biometric / caller-id alone
        if not effective.satisfies(required):
            return decide(PolicyDecisionType.REQUIRE_AUTH,
                          f"Requires {required.value}; session is {effective.value}.",
                          required_auth_state=required, requires_strong_factor=risk.level >= RiskLevel.HIGH.level)

        # 3. risk check: maker-checker
        for r in rules:
            if r.effect == "REQUIRE_HUMAN_APPROVAL":
                approved = bool(grant and grant.approval_id and grant.action_hash == ahash and
                                await self.approvals.is_approved(ctx.tenant_id, grant.approval_id, ahash))
                if not approved:
                    return decide(PolicyDecisionType.REQUIRE_HUMAN_APPROVAL, r.params.get("reason", r.name))

        # 4. explicit customer confirmation bound to these exact arguments
        needs_confirmation = (tool.requires_confirmation or risk.level >= RiskLevel.HIGH.level
                              or any(r.effect == "REQUIRE_CONFIRMATION" for r in rules))
        if needs_confirmation and not (grant and grant.confirmed and grant.action_hash == ahash):
            return decide(PolicyDecisionType.REQUIRE_CONFIRMATION, "Customer confirmation required.")

        return decide(PolicyDecisionType.ALLOW, "Allowed.")
