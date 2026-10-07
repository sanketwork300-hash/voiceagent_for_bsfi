from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import Agent
from app.database.repositories import TenantRepository
from app.domain import new_id

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    persona_prompt: str = ""
    allowed_tools: list[str] | None = None
    channels: list[str] = ["chat", "voice"]
    languages: list[str] = ["en", "hi"]
    voice_config: dict = {}
    llm_config: dict = {}


def _view(a: Agent) -> dict:
    return {"id": a.id, "tenant_id": a.tenant_id, "name": a.name, "description": a.description, "persona_prompt": a.persona_prompt,
            "allowed_tools": a.allowed_tools, "channels": a.channels, "languages": a.languages, "voice_config": a.voice_config,
            "is_active": a.is_active}


@router.post("", status_code=201)
async def create_agent(body: AgentCreate, p: StaffPrincipal = Depends(require(Permission.AGENT_MANAGE)), c=Depends(container)) -> dict:
    async with c.db.session() as s:
        a = await TenantRepository(s, Agent, p.tenant_id).add(Agent(id=new_id(), **body.model_dump()))
    c.directory.invalidate()
    await c.audit.record(p.tenant_id, "agent.created", actor_type="staff", actor_id=p.user_id, resource=a.id)
    return _view(a)


@router.get("")
async def list_agents(p: StaffPrincipal = Depends(require(Permission.AGENT_READ)), c=Depends(container)) -> list[dict]:
    async with c.db.session() as s:
        return [_view(a) for a in await TenantRepository(s, Agent, p.tenant_id).list(order_by=Agent.created_at)]


@router.get("/{agent_id}")
async def get_agent(agent_id: str, p: StaffPrincipal = Depends(require(Permission.AGENT_READ)), c=Depends(container)) -> dict:
    async with c.db.session() as s:
        a = await TenantRepository(s, Agent, p.tenant_id).get(agent_id)
    if a is None:
        raise HTTPException(404, "agent not found")
    return _view(a)
