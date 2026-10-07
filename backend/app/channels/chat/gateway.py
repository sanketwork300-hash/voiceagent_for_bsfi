"""Chat gateway: REST message handling (non-streaming) on top of the shared runtime."""

from __future__ import annotations

from app.agents.runtime import AgentRuntime
from app.channels.base import build_request
from app.domain import AgentResponse, Channel, OutputModality
from app.sessions.manager import SessionManager


class ChatGateway:
    channel = Channel.CHAT

    def __init__(self, runtime: AgentRuntime, sessions: SessionManager) -> None:
        self.runtime = runtime
        self.sessions = sessions

    async def ensure_chat_channel(self, session_id: str, tenant_id: str):
        state = await self.sessions.get(session_id, tenant_id)
        if state.channel != Channel.CHAT:  # channel switch voice -> chat keeps the same conversation
            state = await self.sessions.attach_channel(session_id, tenant_id, Channel.CHAT)
        return state

    async def handle_message(self, *, session_id: str, tenant_id: str, text: str, language_hint: str | None = None) -> AgentResponse:
        state = await self.ensure_chat_channel(session_id, tenant_id)
        req = build_request(state, text, channel=Channel.CHAT, modality=OutputModality.TEXT, language_hint=language_hint)
        return await self.runtime.process(req)
