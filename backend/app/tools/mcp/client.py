"""Minimal MCP client (initialize / tools/list / tools/call) plus the tool executor built on it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from sqlalchemy import select

from app.database.models import MCPServer
from app.database.session import Database
from app.integrations.credentials import CredentialManager
from app.tools.mcp.transport import (
    PROTOCOL_VERSION,
    MCPTransport,
    MCPTransportError,
    StdioTransport,
    StreamableHTTPTransport,
)
from app.tools.schemas import ToolContext, ToolDefinition, ToolExecutionError


class MCPClient:
    def __init__(self, transport: MCPTransport) -> None:
        self.transport = transport
        self.server_info: dict[str, Any] = {}
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> dict[str, Any]:
        async with self._lock:
            if self._initialized:
                return self.server_info
            res = await self.transport.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "bfsi-agent-platform", "version": "0.1.0"}})
            if isinstance(self.transport, StreamableHTTPTransport) and res.get("protocolVersion"):
                self.transport.protocol_version = res["protocolVersion"]
            await self.transport.notify("notifications/initialized")
            self.server_info = res
            self._initialized = True
            return res

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.initialize()
        tools, cursor = [], None
        while True:
            res = await self.transport.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
        await self.initialize()
        return await self.transport.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)

    async def aclose(self) -> None:
        await self.transport.aclose()


def parse_tool_result(res: dict[str, Any]) -> Any:
    if res.get("structuredContent") is not None:
        data = res["structuredContent"]
    else:
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            data = json.loads(joined)
        except json.JSONDecodeError:
            data = {"text": joined}
    if res.get("isError"):
        msg = data.get("error") if isinstance(data, dict) else None
        raise ToolExecutionError(str(msg or data)[:300])
    return data


class MCPToolExecutor:
    """Keeps one initialised MCP session per (tenant, server)."""

    def __init__(self, db: Database, credentials: CredentialManager, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.db = db
        self.credentials = credentials
        self.http_transport = http_transport
        self._clients: dict[tuple[str, str], MCPClient] = {}

    async def client_for(self, tenant_id: str, server_id: str) -> MCPClient:
        key = (tenant_id, server_id)
        if key in self._clients:
            return self._clients[key]
        async with self.db.session() as s:
            server = (await s.execute(select(MCPServer).where(
                MCPServer.tenant_id == tenant_id, MCPServer.id == server_id))).scalar_one_or_none()
        if server is None or not server.is_enabled:
            raise ToolExecutionError("MCP server not available")
        headers: dict[str, str] = {}
        if server.integration_id:
            cfg = await self.credentials.get(tenant_id, server.integration_id)
            headers = {**cfg.config.get("headers", {}), **await self.credentials.auth_headers(cfg)}
        if server.transport == "stdio":
            transport: MCPTransport = StdioTransport(server.command or [])
        else:
            transport = StreamableHTTPTransport(server.url or "", headers=headers, transport=self.http_transport)
        client = MCPClient(transport)
        self._clients[key] = client
        return client

    def drop(self, tenant_id: str, server_id: str) -> None:
        self._clients.pop((tenant_id, server_id), None)

    async def execute(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext) -> Any:
        server_id = tool.binding["server_id"]
        client = await self.client_for(ctx.tenant_id, server_id)
        try:
            res = await client.call_tool(tool.binding.get("remote_name", tool.name), args, timeout=tool.timeout_seconds)
        except MCPTransportError as e:
            self.drop(ctx.tenant_id, server_id)  # force re-initialise next time (session may have expired)
            raise ToolExecutionError(str(e), retryable=True) from e
        return parse_tool_result(res)

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()
