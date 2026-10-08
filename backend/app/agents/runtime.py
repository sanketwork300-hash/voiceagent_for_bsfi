"""AgentRuntime: the single entry point shared by every interaction channel.

Channels (chat REST/WebSocket, LiveKit voice, evaluation harness) build a normalized `AgentRequest` and
consume `RuntimeEvent`s / an `AgentResponse`. The runtime has no channel-specific business logic: the
`channel` field is used for metrics/audit/policy context only, and `output_modality` only adjusts the
style instructions given to the LLM.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from sqlalchemy import select

from app.agents.orchestrator import AgentProfile, Orchestrator
from app.database.models import Agent, Tenant
from app.database.session import Database
from app.domain import AgentRequest, AgentResponse, Channel, RuntimeEvent


class AgentDirectory:
    def __init__(self, db: Database, ttl: float = 30.0) -> None:
        self.db = db
        self.ttl = ttl
        self._cache: dict[tuple[str, str | None], tuple[float, AgentProfile]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    async def get(self, tenant_id: str, agent_id: str | None) -> AgentProfile:
        key = (tenant_id, agent_id)
        if (hit := self._cache.get(key)) and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        async with self.db.session() as s:
            tenant = await s.get(Tenant, tenant_id)
            agent = None
            if agent_id:
                agent = (await s.execute(select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == agent_id))).scalar_one_or_none()
            if agent is None:
                agent = (await s.execute(select(Agent).where(Agent.tenant_id == tenant_id, Agent.is_active.is_(True))
                                         .order_by(Agent.created_at).limit(1))).scalar_one_or_none()
        if tenant is None:
            raise LookupError("tenant not found")
        profile = AgentProfile(
            tenant_id=tenant_id, tenant_slug=tenant.slug, tenant_name=tenant.name, institution_type=tenant.institution_type,
            agent_id=agent.id if agent else None, agent_name=agent.name if agent else "Assistant",
            persona=agent.persona_prompt if agent else "", allowed_tools=agent.allowed_tools if agent else None,
            voice_config=(agent.voice_config or {}) if agent else {},
        )
        self._cache[key] = (time.monotonic(), profile)
        return profile


class AgentRuntime:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def stream(self, request: AgentRequest) -> AsyncIterator[RuntimeEvent]:
        """Streaming turn. Cancelling the consumer (barge-in) persists the partial answer as interrupted."""
        return self.orchestrator.run(request)

    async def end_channel(self, session_id: str, tenant_id: str, *, channel: Channel, close: bool, reason: str) -> dict:
        """Channel ended (hangup / disconnect): safe cleanup, decided by the runtime (see Orchestrator.end_channel)."""
        return await self.orchestrator.end_channel(session_id, tenant_id, channel=channel, close=close, reason=reason)

    async def process(self, request: AgentRequest) -> AgentResponse:
        response: AgentResponse | None = None
        async for ev in self.stream(request):
            if ev.type == "message.completed":
                response = ev.response
        assert response is not None
        return response
