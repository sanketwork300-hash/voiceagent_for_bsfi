"""Per-tenant tool registry: built-ins + REST/OpenAPI tools + MCP tools, all as `ToolDefinition`."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from app.database.models import APITool, Integration, MCPServer, MCPTool
from app.database.session import Database
from app.domain import AuthState, Intent, RiskLevel
from app.tools.schemas import (
    ExecutionMetadata,
    OperationType,
    SideEffect,
    ToolContext,
    ToolDefinition,
    ToolSource,
    resolve_execution,
)

BuiltinHandler = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]
ToolsHook = Callable[[dict[str, ToolDefinition]], None]

SEARCH_KNOWLEDGE = ToolDefinition(
    name="search_knowledge",
    description=("Search the institution's approved documents (product policies, fees, interest rates, FAQs, "
                 "terms, KYC and regulatory material). Use for general product/policy questions, never for a "
                 "customer's own account data. Returns numbered sources to cite as [n]."),
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Self-contained search query, in English where possible"},
        "product": {"type": "string", "description": "Optional product filter, e.g. home_loan, credit_card, savings_account"},
    }, "required": ["query"]},
    risk_level=RiskLevel.LOW, min_auth_state=AuthState.UNAUTHENTICATED, source=ToolSource.BUILTIN,
    execution=ExecutionMetadata(operation_type=OperationType.READ, side_effect=SideEffect.NONE, parallel_safe=True,
                                idempotent=True, concurrency_group="knowledge"),
    intents=[Intent.KNOWLEDGE_QUERY, Intent.GENERAL_CONVERSATION, Intent.CUSTOMER_DATA_QUERY, Intent.ACTION_REQUEST],
)
REQUEST_HANDOFF = ToolDefinition(
    name="request_human_handoff",
    description="Transfer the conversation to a human agent (customer asks for a person, complex complaint, or you cannot help).",
    input_schema={"type": "object", "properties": {
        "reason": {"type": "string", "enum": ["CUSTOMER_REQUEST", "COMPLEX_COMPLAINT", "FRAUD", "REPEATED_FAILURE"]},
        "note": {"type": "string", "description": "One-line context for the human agent"},
    }, "required": ["reason"]},
    risk_level=RiskLevel.LOW, min_auth_state=AuthState.UNAUTHENTICATED, source=ToolSource.BUILTIN,
    # The handler only acknowledges; the runtime starts the handoff after the plan's other steps have run.
    execution=ExecutionMetadata(operation_type=OperationType.READ, side_effect=SideEffect.NONE, parallel_safe=True,
                                idempotent=True),
)


class ToolRegistry:
    def __init__(self, db: Database, cache_ttl_seconds: float = 30.0) -> None:
        self.db = db
        self._builtins: dict[str, tuple[ToolDefinition, BuiltinHandler]] = {}
        self._cache: dict[str, tuple[float, dict[str, ToolDefinition]]] = {}
        self._ttl = cache_ttl_seconds
        self._hooks: list[ToolsHook] = []

    def add_hook(self, hook: ToolsHook) -> None:
        """Post-load hook, e.g. workflow templates marking workflow-bound parameters."""
        self._hooks.append(hook)

    def register_builtin(self, tool: ToolDefinition, handler: BuiltinHandler) -> None:
        self._builtins[tool.name] = (tool, handler)

    def builtin_handler(self, name: str) -> BuiltinHandler | None:
        entry = self._builtins.get(name)
        return entry[1] if entry else None

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)

    async def tools_for(self, tenant_id: str) -> dict[str, ToolDefinition]:
        hit = self._cache.get(tenant_id)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        tools: dict[str, ToolDefinition] = {name: t for name, (t, _) in self._builtins.items()}
        async with self.db.session() as s:
            api_rows = (await s.execute(
                select(APITool, Integration.kind).join(Integration, Integration.id == APITool.integration_id)
                .where(APITool.tenant_id == tenant_id, Integration.tenant_id == tenant_id, Integration.is_enabled.is_(True))
            )).all()
            mcp_rows = (await s.execute(
                select(MCPTool).join(MCPServer, MCPServer.id == MCPTool.server_id)
                .where(MCPTool.tenant_id == tenant_id, MCPServer.tenant_id == tenant_id, MCPServer.is_enabled.is_(True))
            )).scalars().all()
        for row, kind in api_rows:
            tools[row.name] = ToolDefinition(
                name=row.name, description=row.description, input_schema=row.input_schema,
                risk_level=RiskLevel(row.risk_level), min_auth_state=AuthState(row.min_auth_state),
                requires_confirmation=row.requires_confirmation,
                source=ToolSource.OPENAPI if kind == "openapi" else ToolSource.ADAPTER if kind == "adapter" else ToolSource.REST,
                integration_id=row.integration_id,
                binding={"method": row.method, "path": row.path, "parameter_map": row.parameter_map, "tool_id": row.id},
                injected_params=row.injected_params, intents=[Intent(i) for i in row.intents],
                confirmation_template=row.confirmation_template, timeout_seconds=row.timeout_seconds,
                idempotent=row.idempotent, internal=row.internal, enabled=row.is_enabled,
                execution=resolve_execution(source=ToolSource.REST, binding={"method": row.method}, idempotent=row.idempotent,
                                            internal=row.internal, declared=row.execution, default_group=row.integration_id),
            )
        for row in mcp_rows:
            tools[row.name] = ToolDefinition(
                name=row.name, description=row.description, input_schema=row.input_schema,
                risk_level=RiskLevel(row.risk_level), min_auth_state=AuthState(row.min_auth_state),
                requires_confirmation=row.requires_confirmation, source=ToolSource.MCP,
                binding={"server_id": row.server_id, "remote_name": row.remote_name, "tool_id": row.id},
                injected_params=row.injected_params, intents=[Intent(i) for i in row.intents],
                confirmation_template=row.confirmation_template, timeout_seconds=row.timeout_seconds,
                internal=row.internal, enabled=row.is_enabled,
                idempotent=bool((row.execution or {}).get("idempotent", False)),
                execution=resolve_execution(source=ToolSource.MCP, binding={}, idempotent=False, internal=row.internal,
                                            declared=row.execution, read_hint=bool((row.annotations or {}).get("readOnlyHint")),
                                            default_group=row.server_id),
            )
        # Built-ins are shared objects; hooks may annotate them, so hand out per-tenant copies.
        tools = {n: (t.model_copy(deep=True) if t.source == ToolSource.BUILTIN else t) for n, t in tools.items()}
        for hook in self._hooks:
            hook(tools)
        self._cache[tenant_id] = (time.monotonic(), tools)
        return tools

    async def get(self, tenant_id: str, name: str) -> ToolDefinition | None:
        return (await self.tools_for(tenant_id)).get(name)
