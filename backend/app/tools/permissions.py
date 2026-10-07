"""Which tools may be *offered* to the LLM for a turn (the policy engine still gates execution)."""

from __future__ import annotations

from app.domain import Intent
from app.tools.schemas import ToolDefinition


def offerable_tools(tools: dict[str, ToolDefinition], *, agent_allowlist: list[str] | None,
                    intent: Intent | None) -> dict[str, ToolDefinition]:
    out: dict[str, ToolDefinition] = {}
    for name, t in tools.items():
        if t.internal or not t.enabled:
            continue
        if agent_allowlist is not None and name not in agent_allowlist and t.source.value != "builtin":
            continue
        if intent and t.intents and intent not in t.intents:
            continue
        out[name] = t
    return out
