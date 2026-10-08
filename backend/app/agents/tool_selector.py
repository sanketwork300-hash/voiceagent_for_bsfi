"""Intent-scoped tool offering.

Least privilege per turn: state-changing tools are only offered when the turn is an action/fraud request,
so a knowledge question containing injected instructions cannot even see `transfer_money`.
"""

from __future__ import annotations

from app.domain import Intent, RiskLevel
from app.tools.permissions import offerable_tools
from app.tools.schemas import ToolDefinition

ALWAYS = {"request_human_handoff"}


def _is_action(t: ToolDefinition) -> bool:
    # any state-changing tool counts, even if mis-registered as low risk without confirmation
    return t.requires_confirmation or t.risk_level.level >= RiskLevel.HIGH.level or t.exec.mutates


def select_tools(tools: dict[str, ToolDefinition], *, intent: Intent, agent_allowlist: list[str] | None) -> dict[str, ToolDefinition]:
    candidates = offerable_tools(tools, agent_allowlist=agent_allowlist, intent=None)
    match intent:
        case Intent.KNOWLEDGE_QUERY:
            keep = {n for n, t in candidates.items() if n == "search_knowledge" or not _is_action(t)}
        case Intent.CUSTOMER_DATA_QUERY | Intent.GENERAL_CONVERSATION:
            keep = {n for n, t in candidates.items() if not _is_action(t)}
        case Intent.ACTION_REQUEST:
            keep = set(candidates)
        case Intent.FRAUD_REQUEST:
            keep = {n for n, t in candidates.items() if not _is_action(t) or n == "block_card"}
        case Intent.HUMAN_HANDOFF:
            keep = set()
    keep |= ALWAYS & set(candidates)
    return {n: t for n, t in candidates.items() if n in keep and (not t.intents or intent in t.intents or n in ALWAYS)}
