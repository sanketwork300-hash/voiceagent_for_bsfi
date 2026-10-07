"""Declarative policy rules. Tenants store these in the `policies` table; defaults live here."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.domain import AuthState, RiskLevel

RULE_EFFECTS = {"DENY", "REQUIRE_AUTH", "REQUIRE_CONFIRMATION", "REQUIRE_HUMAN_APPROVAL", "SET_MIN_AUTH"}


class PolicyRule(BaseModel):
    """
    conditions (all must hold; omitted keys match anything):
      tool: str | list[str]          intent: str | list[str]          channel: str | list[str]
      risk_level_gte: RiskLevel      auth_state_lt: AuthState          source: str | list[str]
      args: {name: {gt|gte|lt|lte|eq|ne|in: value}}
      session: {stat_name: {...same operators...}}  e.g. transfer_total_today
      args_plus_session: {"arg": "amount", "stat": "transfer_total_today", "gt": 1000000}
    """

    id: str
    name: str
    priority: int = 100
    conditions: dict[str, Any] = Field(default_factory=dict)
    effect: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> PolicyRule:
        if self.effect not in RULE_EFFECTS:
            raise ValueError(f"unknown effect {self.effect}")
        known = {"tool", "intent", "channel", "risk_level_gte", "auth_state_lt", "source", "args", "session", "args_plus_session"}
        if unknown := set(self.conditions) - known:
            raise ValueError(f"unknown condition keys {sorted(unknown)}")
        ops = {"gt", "gte", "lt", "lte", "eq", "ne", "in"}
        for group in ("args", "session"):
            for name, spec in (self.conditions.get(group) or {}).items():
                if not isinstance(spec, dict) or not spec or set(spec) - ops:
                    raise ValueError(f"invalid operators for {group}.{name}; allowed: {sorted(ops)}")
        if aps := self.conditions.get("args_plus_session"):
            if not {"arg", "stat"} <= set(aps) or set(aps) - {"arg", "stat"} - ops:
                raise ValueError("args_plus_session needs 'arg', 'stat' and comparison operators")
        if self.effect == "SET_MIN_AUTH":
            AuthState(self.params.get("min_auth_state", ""))
        if "risk_level_gte" in self.conditions:
            RiskLevel(self.conditions["risk_level_gte"])
        return self


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


_NUMERIC_OPS = {"gt": float.__gt__, "gte": float.__ge__, "lt": float.__lt__, "lte": float.__le__}


def _cmp(value: Any, spec: dict[str, Any]) -> bool:
    try:
        for op, target in spec.items():
            if op in _NUMERIC_OPS:
                ok = value is not None and _NUMERIC_OPS[op](float(value), float(target))
            elif op == "eq":
                ok = value == target
            elif op == "ne":
                ok = value != target
            elif op == "in":
                ok = value in _as_list(target)
            else:
                return False  # unreachable for stored rules: PolicyRule rejects unknown operators
            if not ok:
                return False
        return True
    except (TypeError, ValueError):
        return False


def rule_matches(rule: PolicyRule, *, tool: str, source: str, risk: RiskLevel, intent: str | None, channel: str,
                 auth_state: AuthState, args: dict[str, Any], session: dict[str, Any]) -> bool:
    c = rule.conditions
    if "tool" in c and tool not in _as_list(c["tool"]):
        return False
    if "source" in c and source not in _as_list(c["source"]):
        return False
    if "intent" in c and intent not in _as_list(c["intent"]):
        return False
    if "channel" in c and channel not in _as_list(c["channel"]):
        return False
    if "risk_level_gte" in c and risk.level < RiskLevel(c["risk_level_gte"]).level:
        return False
    if "auth_state_lt" in c and auth_state.level >= AuthState(c["auth_state_lt"]).level:
        return False
    for name, spec in (c.get("args") or {}).items():
        if not _cmp(args.get(name), spec):
            return False
    for name, spec in (c.get("session") or {}).items():
        if not _cmp(session.get(name, 0), spec):
            return False
    if aps := c.get("args_plus_session"):
        total = float(args.get(aps["arg"]) or 0) + float(session.get(aps["stat"]) or 0)
        if not _cmp(total, {k: v for k, v in aps.items() if k not in ("arg", "stat")}):
            return False
    return True


DEFAULT_RULES: list[PolicyRule] = [
    PolicyRule(
        id="default.protective_block_in_fraud", name="Allow card block at partial auth during fraud",
        priority=10, conditions={"tool": "block_card", "intent": "FRAUD_REQUEST"}, effect="SET_MIN_AUTH",
        params={"min_auth_state": AuthState.PARTIALLY_AUTHENTICATED.value},
    ),
    PolicyRule(
        id="default.transfer_daily_cap", name="Daily transfer cap per conversation (₹10,00,000)", priority=20,
        conditions={"tool": "transfer_money", "args_plus_session": {"arg": "amount", "stat": "transfer_total_today", "gt": 1_000_000}},
        effect="DENY", params={"reason": "This exceeds the daily transfer limit available through the assistant."},
    ),
    PolicyRule(
        id="default.transfer_maker_checker", name="Transfers above ₹5,00,000 need human approval", priority=30,
        conditions={"tool": "transfer_money", "args": {"amount": {"gt": 500_000}}}, effect="REQUIRE_HUMAN_APPROVAL",
        params={"reason": "High-value transfer requires maker-checker approval."},
    ),
    PolicyRule(
        id="default.money_movement_confirmation", name="Money movement always needs explicit confirmation", priority=40,
        conditions={"tool": ["transfer_money", "block_card"]}, effect="REQUIRE_CONFIRMATION",
    ),
]
