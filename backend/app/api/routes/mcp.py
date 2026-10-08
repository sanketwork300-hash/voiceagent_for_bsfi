from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import Integration, MCPServer, MCPTool

router = APIRouter(prefix="/mcp", tags=["integrations"])


class MCPServerCreate(BaseModel):
    name: str
    url: str | None = None
    transport: str = "streamable_http"
    command: list[str] | None = None
    integration_id: str | None = None  # supplies credentials/headers
    auto_enable: bool = False


def _tool(t) -> dict:
    return {"id": t.id, "name": t.name, "remote_name": t.remote_name, "description": t.description, "input_schema": t.input_schema,
            "risk_level": t.risk_level, "min_auth_state": t.min_auth_state, "requires_confirmation": t.requires_confirmation,
            "injected_params": t.injected_params, "is_enabled": t.is_enabled, "annotations": t.annotations}


@router.post("/servers", status_code=201)
async def register_server(body: MCPServerCreate, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)), c=Depends(container)) -> dict:
    server, tools = await c.integrations.register_mcp_server(p.tenant_id, **body.model_dump())
    await c.audit.record(p.tenant_id, "mcp.server_registered", actor_type="staff", actor_id=p.user_id, resource=server.id,
                         payload={"tools": [t.name for t in tools]})
    return {"id": server.id, "name": server.name, "tools": [_tool(t) for t in tools]}


@router.get("/servers")
async def list_servers(p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)), c=Depends(container)) -> list[dict]:
    async with c.db.session() as s:
        servers = (await s.execute(select(MCPServer).where(MCPServer.tenant_id == p.tenant_id))).scalars().all()
        counts = dict((await s.execute(select(MCPTool.server_id, func.count()).where(MCPTool.tenant_id == p.tenant_id)
                                       .group_by(MCPTool.server_id))).all())
        integ = {i.id: i for i in (await s.execute(select(Integration).where(Integration.tenant_id == p.tenant_id))).scalars()}
    return [{"id": x.id, "name": x.name, "transport": x.transport, "url": x.url, "integration_id": x.integration_id,
             "auth_type": integ[x.integration_id].auth_type if x.integration_id in integ else "none",
             "status": integ[x.integration_id].status if x.integration_id in integ else "unknown",
             "protocol_version": x.protocol_version, "server_info": x.server_info, "tool_count": counts.get(x.id, 0),
             "last_discovered_at": x.last_discovered_at, "is_enabled": x.is_enabled} for x in servers]


@router.get("/servers/{server_id}/tools")
async def server_tools(server_id: str, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)), c=Depends(container)) -> list[dict]:
    return [_tool(t) for t in await c.integrations.list_mcp_tools(p.tenant_id, server_id)]


@router.post("/servers/{server_id}/discover")
async def rediscover(server_id: str, p: StaffPrincipal = Depends(require(Permission.INTEGRATION_MANAGE)), c=Depends(container)) -> list[dict]:
    return [_tool(t) for t in await c.integrations.discover(p.tenant_id, server_id)]
