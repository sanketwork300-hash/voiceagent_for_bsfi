"""Discover tools from an MCP server and register them (disabled until reviewed, unless auto-enabled)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database.models import MCPServer, MCPTool
from app.database.session import Database
from app.domain import AuthState, RiskLevel, new_id, utcnow
from app.tools.mcp.client import MCPClient


def governance_from_annotations(tool: dict[str, Any]) -> dict[str, Any]:
    """Map MCP annotations (+ optional `_meta.bfsi`) onto platform governance fields.

    Server-provided hints are only a starting point: an MCP server is not trusted to lower its own
    risk, so `readOnlyHint` can never take a tool below MEDIUM and admins can override after discovery.
    """
    ann = tool.get("annotations") or {}
    meta = (tool.get("_meta") or {}).get("bfsi", {})
    if ann.get("destructiveHint") or not ann.get("readOnlyHint", False):
        risk, confirm = RiskLevel.HIGH, True
    else:
        risk, confirm = RiskLevel.MEDIUM, False
    if meta.get("risk_level"):
        declared = RiskLevel(meta["risk_level"])
        risk = max(declared, RiskLevel.MEDIUM, key=lambda r: r.level) if declared.level < risk.level else declared
    props = (tool.get("inputSchema") or {}).get("properties", {})
    injected = meta.get("injected_params") or ({"customer_id": "customer_id"} if "customer_id" in props else {})
    return {
        "risk_level": risk.value,
        "min_auth_state": meta.get("min_auth_state", AuthState.FULLY_AUTHENTICATED.value),
        "requires_confirmation": bool(meta.get("requires_confirmation", confirm)),
        "injected_params": injected,
        "intents": meta.get("intents", []),
        "confirmation_template": meta.get("confirmation_template"),
        "internal": bool(meta.get("internal", False)),
        # scheduling hints (operation_type, side_effect, idempotent, concurrency_group, ...): resolve_execution
        # merges them with readOnlyHint conservatively at load time
        "execution": dict(meta.get("execution") or {}),
    }


async def discover_and_register(db: Database, client: MCPClient, server: MCPServer, *, auto_enable: bool = False) -> list[MCPTool]:
    info = await client.initialize()
    remote_tools = await client.list_tools()
    out: list[MCPTool] = []
    async with db.session() as s:
        existing = {t.remote_name: t for t in (await s.execute(select(MCPTool).where(
            MCPTool.tenant_id == server.tenant_id, MCPTool.server_id == server.id))).scalars()}
        for rt in remote_tools:
            gov = governance_from_annotations(rt)
            row = existing.get(rt["name"])
            if row is None:
                row = MCPTool(id=new_id(), tenant_id=server.tenant_id, server_id=server.id, remote_name=rt["name"],
                              name=rt["name"], is_enabled=auto_enable, **gov)
                s.add(row)
            # schema/description refresh; governance fields are left as the admin configured them
            if not row.execution and gov["execution"]:
                row.execution = gov["execution"]
            row.description = rt.get("description", "")
            row.input_schema = rt.get("inputSchema") or {"type": "object", "properties": {}}
            row.annotations = rt.get("annotations") or {}
            out.append(row)
        srv = await s.get(MCPServer, server.id)
        if srv:
            srv.server_info = info.get("serverInfo", {})
            srv.protocol_version = info.get("protocolVersion")
            srv.last_discovered_at = utcnow()
    return out
