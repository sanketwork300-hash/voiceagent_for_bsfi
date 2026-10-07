"""Contextual risk scoring. A tool's static risk level is a floor; context can only raise it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import Intent, RiskLevel

MONEY_MOVEMENT = {"transfer_money", "pay_bill", "add_beneficiary"}


@dataclass
class RiskAssessment:
    level: RiskLevel
    score: float
    factors: list[str] = field(default_factory=list)


def _level_for(score: float) -> RiskLevel:
    if score >= 0.75:
        return RiskLevel.CRITICAL
    if score >= 0.5:
        return RiskLevel.HIGH
    if score >= 0.25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class RiskScorer:
    def assess(self, *, tool: str, base: RiskLevel, args: dict[str, Any], channel: str, intent: Intent | None,
               session: dict[str, Any]) -> RiskAssessment:
        score = {RiskLevel.LOW: 0.1, RiskLevel.MEDIUM: 0.3, RiskLevel.HIGH: 0.55, RiskLevel.CRITICAL: 0.8}[base]
        factors: list[str] = [f"base:{base.value}"]
        amount = args.get("amount")
        if tool in MONEY_MOVEMENT and isinstance(amount, int | float):
            for threshold, bump, label in ((500_000, 0.2, "amount>5L"), (100_000, 0.1, "amount>=1L"), (25_000, 0.05, "amount>25k")):
                if amount >= threshold:
                    score += bump
                    factors.append(label)
                    break
            if session.get("transfer_count_today", 0) >= 2:
                score += 0.1
                factors.append("velocity")
            if session.get("fraud_flagged"):
                score += 0.2
                factors.append("fraud_flag_in_session")
        if session.get("auth_failures", 0) >= 1:
            score += 0.1 * min(3, session["auth_failures"])
            factors.append("recent_auth_failures")
        if intent == Intent.FRAUD_REQUEST and tool in MONEY_MOVEMENT:
            score += 0.2
            factors.append("money_movement_during_fraud_report")
        score = min(1.0, score)
        level = max(base, _level_for(score), key=lambda r: r.level)
        return RiskAssessment(level=level, score=round(score, 3), factors=factors)
