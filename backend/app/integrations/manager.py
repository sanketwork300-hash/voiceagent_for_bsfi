"""Lifecycle of institution integrations: register, import tools (OpenAPI / MCP), test, enable."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from app.database.models import APITool, Integration, MCPServer, MCPTool
from app.database.session import Database
from app.domain import new_id, utcnow
from app.integrations.credentials import CredentialManager
from app.tools.mcp.client import MCPClient, MCPToolExecutor
from app.tools.mcp.discovery import discover_and_register
from app.tools.mcp.transport import StreamableHTTPTransport
from app.tools.registry import ToolRegistry
from app.tools.rest.openapi import import_openapi
from app.tools.schemas import ToolDefinition


class IntegrationManager:
    def __init__(self, db: Database, credentials: CredentialManager, registry: ToolRegistry, mcp: MCPToolExecutor,
                 http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.db = db
        self.credentials = credentials
        self.registry = registry
        self.mcp = mcp
        self.http_transport = http_transport

    async def create(self, tenant_id: str, *, name: str, kind: str, base_url: str | None, auth_type: str = "none",
                     credentials: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> Integration:
        row = Integration(id=new_id(), tenant_id=tenant_id, name=name, kind=kind, base_url=base_url, auth_type=auth_type,
                          credentials_encrypted=self.credentials.encrypt(credentials) if credentials else None,
                          config=config or {})
        async with self.db.session() as s:
            s.add(row)
        return row

    async def _get(self, tenant_id: str, integration_id: str) -> Integration:
        async with self.db.session() as s:
            row = (await s.execute(select(Integration).where(Integration.tenant_id == tenant_id,
                                                             Integration.id == integration_id))).scalar_one_or_none()
        if row is None:
            raise LookupError("integration not found")
        return row

    async def import_openapi(self, tenant_id: str, integration_id: str, spec: dict[str, Any] | None = None,
                             only: set[str] | None = None, enable: bool = False) -> list[ToolDefinition]:
        integ = await self._get(tenant_id, integration_id)
        if spec is None:
            cfg = await self.credentials.get(tenant_id, integration_id)
            url = integ.config.get("openapi_url") or f"{(integ.base_url or '').rstrip('/')}/openapi.json"
            async with httpx.AsyncClient(transport=self.http_transport, timeout=15) as c:
                r = await c.get(url, headers=await self.credentials.auth_headers(cfg))
                r.raise_for_status()
                spec = r.json()
        tools = [t for t in import_openapi(spec, integration_id=integration_id) if not only or t.name in only]
        async with self.db.session() as s:
            existing = {t.name: t for t in (await s.execute(select(APITool).where(APITool.tenant_id == tenant_id))).scalars()}
            for t in tools:
                row = existing.get(t.name) or APITool(id=new_id(), tenant_id=tenant_id, name=t.name, is_enabled=enable or t.internal)
                row.integration_id = integration_id
                row.description, row.input_schema = t.description, t.input_schema
                row.risk_level, row.min_auth_state = t.risk_level.value, t.min_auth_state.value
                row.requires_confirmation, row.injected_params = t.requires_confirmation, t.injected_params
                row.intents, row.confirmation_template = [i.value for i in t.intents], t.confirmation_template
                row.method, row.path = t.binding["method"], t.binding["path"]
                row.operation_id, row.parameter_map = t.binding.get("operation_id"), t.binding["parameter_map"]
                row.idempotent, row.internal = t.idempotent, t.internal
                row.execution = t.exec.model_dump(mode="json", exclude={"concurrency_group"} if t.exec.concurrency_group == integration_id else None)
                if row.name not in existing:
                    s.add(row)
        self.registry.invalidate(tenant_id)
        return tools

    async def register_mcp_server(self, tenant_id: str, *, name: str, url: str | None, transport: str = "streamable_http",
                                  command: list[str] | None = None, integration_id: str | None = None,
                                  auto_enable: bool = False) -> tuple[MCPServer, list[MCPTool]]:
        server = MCPServer(id=new_id(), tenant_id=tenant_id, name=name, url=url, transport=transport, command=command,
                           integration_id=integration_id)
        async with self.db.session() as s:
            s.add(server)
        tools = await self.discover(tenant_id, server.id, auto_enable=auto_enable)
        return server, tools

    async def discover(self, tenant_id: str, server_id: str, auto_enable: bool = False) -> list[MCPTool]:
        self.mcp.drop(tenant_id, server_id)
        client = await self.mcp.client_for(tenant_id, server_id)
        async with self.db.session() as s:
            server = await s.get(MCPServer, server_id)
        tools = await discover_and_register(self.db, client, server, auto_enable=auto_enable)
        self.registry.invalidate(tenant_id)
        return tools

    async def list_mcp_tools(self, tenant_id: str, server_id: str) -> list[MCPTool]:
        async with self.db.session() as s:
            return list((await s.execute(select(MCPTool).where(MCPTool.tenant_id == tenant_id,
                                                               MCPTool.server_id == server_id))).scalars())

    async def test(self, tenant_id: str, integration_id: str) -> dict[str, Any]:
        integ = await self._get(tenant_id, integration_id)
        cfg = await self.credentials.get(tenant_id, integration_id)
        started = utcnow()
        ok, detail = False, ""
        try:
            if integ.kind == "mcp":
                t = StreamableHTTPTransport(integ.base_url or "", headers=await self.credentials.auth_headers(cfg),
                                            transport=self.http_transport)
                client = MCPClient(t)
                info = await client.initialize()
                detail = f"MCP {info.get('protocolVersion')} {info.get('serverInfo', {}).get('name', '')}"
                await client.aclose()
                ok = True
            else:
                path = integ.config.get("health_path", "/health")
                async with httpx.AsyncClient(transport=self.http_transport, timeout=5) as c:
                    r = await c.get(f"{(integ.base_url or '').rstrip('/')}{path}", headers=await self.credentials.auth_headers(cfg))
                ok, detail = r.status_code < 400, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            detail = type(e).__name__
        async with self.db.session() as s:
            row = await s.get(Integration, integration_id)
            if row:
                row.status = "healthy" if ok else "down"
                row.last_checked_at = utcnow()
        return {"ok": ok, "detail": detail, "latency_ms": round((utcnow() - started).total_seconds() * 1000, 1)}
