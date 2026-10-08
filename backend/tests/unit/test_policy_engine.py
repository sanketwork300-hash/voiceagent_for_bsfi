from datetime import date

import pytest

from app.domain import (
    AuthState,
    Channel,
    Intent,
    PolicyDecisionType,
    RiskLevel,
)
from app.policies.approval import ActionGrant, action_hash as _action_hash
from app.policies.risk import RiskScorer
from app.policies.rules import PolicyRule, rule_matches
from app.tools.schemas import ToolContext, ToolDefinition, ToolSource

TRANSFER = ToolDefinition(name="transfer_money", description="t", source=ToolSource.MCP, risk_level=RiskLevel.CRITICAL,
                          min_auth_state=AuthState.TRANSACTION_AUTHENTICATED, requires_confirmation=True,
                          input_schema={"type": "object", "properties": {"amount": {"type": "number"}, "payee_name": {"type": "string"}}})
BLOCK = ToolDefinition(name="block_card", description="b", source=ToolSource.MCP, risk_level=RiskLevel.HIGH,
                       min_auth_state=AuthState.FULLY_AUTHENTICATED, requires_confirmation=True)
BALANCE = ToolDefinition(name="get_account_balance", description="b", source=ToolSource.OPENAPI, risk_level=RiskLevel.MEDIUM,
                         min_auth_state=AuthState.FULLY_AUTHENTICATED)


def action_hash(tool, args):  # bound to the tenant/session/customer of ctx() below
    return _action_hash(tool, args, tenant_id="t1", session_id="s1", customer_id="CUST1")


def ctx(auth=AuthState.FULLY_AUTHENTICATED, methods=("customer_assertion",), intent=Intent.ACTION_REQUEST, txn=None, **stats):
    return ToolContext(tenant_id="t1", session_id="s1", conversation_id="c1", customer_id="CUST1", channel=Channel.CHAT,
                       auth_state=auth, auth_methods=list(methods), intent=intent, txn_auth_action_hash=txn, session_stats=stats)


@pytest.fixture
async def engine(container):
    return container.policy


async def test_read_tool_allowed_when_fully_authenticated(engine):
    d = await engine.evaluate(BALANCE, {}, ctx())
    assert d.decision == PolicyDecisionType.ALLOW


async def test_read_tool_requires_auth_when_unauthenticated(engine):
    d = await engine.evaluate(BALANCE, {}, ctx(auth=AuthState.UNAUTHENTICATED, methods=()))
    assert d.decision == PolicyDecisionType.REQUIRE_AUTH and d.required_auth_state == AuthState.FULLY_AUTHENTICATED


async def test_transfer_flow_auth_then_confirmation_then_allow(engine):
    args = {"amount": 100000, "payee_name": "Rahul"}
    h = action_hash("transfer_money", args)
    assert (await engine.evaluate(TRANSFER, args, ctx())).decision == PolicyDecisionType.REQUIRE_AUTH
    txn_ctx = ctx(auth=AuthState.TRANSACTION_AUTHENTICATED, methods=("customer_assertion", "otp", "transaction_otp"), txn=h)
    assert (await engine.evaluate(TRANSFER, args, txn_ctx)).decision == PolicyDecisionType.REQUIRE_CONFIRMATION
    d = await engine.evaluate(TRANSFER, args, txn_ctx, grant=ActionGrant(action_hash=h, confirmed=True))
    assert d.decision == PolicyDecisionType.ALLOW


async def test_transaction_auth_is_bound_to_one_action(engine):
    a1, a2 = {"amount": 100000, "payee_name": "Rahul"}, {"amount": 200000, "payee_name": "Rahul"}
    txn_ctx = ctx(auth=AuthState.TRANSACTION_AUTHENTICATED, txn=action_hash("transfer_money", a1))
    d = await engine.evaluate(TRANSFER, a2, txn_ctx, grant=ActionGrant(action_hash=action_hash("transfer_money", a1), confirmed=True))
    assert d.decision == PolicyDecisionType.REQUIRE_AUTH


async def test_confirmation_grant_must_match_arguments(engine):
    args = {"amount": 1000, "payee_name": "Rahul"}
    h = action_hash("transfer_money", args)
    c = ctx(auth=AuthState.TRANSACTION_AUTHENTICATED, txn=h)
    d = await engine.evaluate(TRANSFER, args, c, grant=ActionGrant(action_hash="0" * 64, confirmed=True))
    assert d.decision == PolicyDecisionType.REQUIRE_CONFIRMATION


async def test_voice_biometric_alone_insufficient_for_high_risk(engine):
    c = ctx(auth=AuthState.FULLY_AUTHENTICATED, methods=("caller_id", "voice_biometric"))
    d = await engine.evaluate(BLOCK, {"card_type": "credit"}, c)
    assert d.decision == PolicyDecisionType.REQUIRE_AUTH and d.requires_strong_factor


async def test_protective_block_relaxed_during_fraud(engine):
    c = ctx(auth=AuthState.PARTIALLY_AUTHENTICATED, methods=("caller_id", "voice_biometric"), intent=Intent.FRAUD_REQUEST)
    assert (await engine.evaluate(BLOCK, {"card_type": "credit"}, c)).decision == PolicyDecisionType.REQUIRE_CONFIRMATION


async def test_maker_checker_and_daily_cap(engine):
    big = {"amount": 600000, "payee_name": "Rahul"}
    c = ctx(auth=AuthState.TRANSACTION_AUTHENTICATED, txn=action_hash("transfer_money", big))
    assert (await engine.evaluate(TRANSFER, big, c)).decision == PolicyDecisionType.REQUIRE_HUMAN_APPROVAL
    capped = ctx(auth=AuthState.TRANSACTION_AUTHENTICATED, txn=action_hash("transfer_money", big), transfer_total_today=500000)
    assert (await engine.evaluate(TRANSFER, big, capped)).decision == PolicyDecisionType.DENY


async def test_disabled_and_internal_tools_denied(engine):
    off = BALANCE.model_copy(update={"enabled": False})
    assert (await engine.evaluate(off, {}, ctx())).decision == PolicyDecisionType.DENY
    internal = BALANCE.model_copy(update={"internal": True})
    assert (await engine.evaluate(internal, {}, ctx())).decision == PolicyDecisionType.DENY


async def test_agent_allowlist(engine):
    assert (await engine.evaluate(BALANCE, {}, ctx(), allowed_tools={"search_knowledge"})).decision == PolicyDecisionType.DENY


def test_risk_scorer_escalates_with_context():
    s = RiskScorer()
    low = s.assess(tool="transfer_money", base=RiskLevel.HIGH, args={"amount": 1000}, channel="chat", intent=None, session={})
    hi = s.assess(tool="transfer_money", base=RiskLevel.HIGH, args={"amount": 900000}, channel="voice",
                  intent=Intent.FRAUD_REQUEST, session={"transfer_count_today": 3, "fraud_flagged": True})
    assert low.level == RiskLevel.HIGH and hi.level == RiskLevel.CRITICAL and hi.score > low.score


def test_rule_conditions():
    r = PolicyRule(id="r", name="r", effect="DENY", conditions={"tool": "transfer_money", "channel": "voice", "args": {"amount": {"gt": 200000}}})
    kw = dict(tool="transfer_money", source="mcp", risk=RiskLevel.CRITICAL, intent=None, auth_state=AuthState.FULLY_AUTHENTICATED, session={})
    assert rule_matches(r, channel="voice", args={"amount": 250000}, **kw)
    assert not rule_matches(r, channel="chat", args={"amount": 250000}, **kw)
    assert not rule_matches(r, channel="voice", args={"amount": 1000}, **kw)
    _ = date.today()


@pytest.mark.parametrize("conditions,effect", [
    ({"args": {"amount": {"greater_than": 5}}}, "DENY"),
    ({"tool_name": "x"}, "DENY"),
    ({}, "ALLOW_ALL"),
    ({"args_plus_session": {"arg": "amount", "gt": 1}}, "DENY"),
])
def test_malformed_rules_rejected(conditions, effect):
    with pytest.raises(ValueError):
        PolicyRule(id="r", name="r", conditions=conditions, effect=effect)
