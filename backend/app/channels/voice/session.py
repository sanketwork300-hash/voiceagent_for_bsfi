"""Voice sessions: LiveKit room + join tokens bound to the shared session, and the VoiceChannel adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

import jwt

from app.agents.runtime import AgentRuntime
from app.agents.state import SessionState
from app.channels.base import (
    ChannelInput,
    InteractionChannel,
    TurnRunner,
    build_request,
)
from app.channels.voice.audio import speech_text
from app.config import Settings
from app.domain import AgentResponse, Channel, OutputModality, RuntimeEvent
from app.sessions.manager import SessionManager


def livekit_token(*, api_key: str, api_secret: str, identity: str, room: str, name: str | None = None,
                  metadata: dict | None = None, ttl_seconds: int = 900, agent_name: str | None = None,
                  agent_metadata: dict | None = None, can_publish: bool = True) -> str:
    """LiveKit access token (JWT). When `agent_name` is set, joining the room dispatches that agent."""
    now = int(time.time())
    claims = {
        "iss": api_key, "sub": identity, "nbf": now, "exp": now + ttl_seconds, "name": name or identity,
        "metadata": json.dumps(metadata or {}),
        "video": {"room": room, "roomJoin": True, "canPublish": can_publish, "canSubscribe": True, "canPublishData": True},
    }
    if agent_name:
        claims["roomConfig"] = {"agents": [{"agentName": agent_name, "metadata": json.dumps(agent_metadata or {})}]}
    return jwt.encode(claims, api_secret, algorithm="HS256")


class VoiceSessionService:
    def __init__(self, settings: Settings, sessions: SessionManager) -> None:
        self.settings = settings
        self.sessions = sessions

    @staticmethod
    def room_for(state: SessionState) -> str:
        return f"bfsi-{state.tenant_id[:8]}-{state.session_id}"

    async def start(self, session_id: str, tenant_id: str) -> tuple[SessionState, str]:
        """Attach voice to an existing session (chat -> voice switch keeps the conversation)."""
        state = await self.sessions.attach_channel(session_id, tenant_id, Channel.VOICE)
        room = state.voice_room or self.room_for(state)
        if state.voice_room != room:
            async with self.sessions.locked(session_id, tenant_id) as st:
                st.voice_room = room
            state.voice_room = room
        return state, room

    def customer_token(self, state: SessionState, room: str) -> str:
        return livekit_token(
            api_key=self.settings.livekit_api_key, api_secret=self.settings.livekit_api_secret,
            identity=f"customer-{state.session_id}", room=room, name="Customer",
            metadata={"session_id": state.session_id}, ttl_seconds=self.settings.voice_max_call_seconds,
            agent_name=self.settings.livekit_agent_name,
            agent_metadata={"session_id": state.session_id, "tenant_id": state.tenant_id},
        )


class VoiceChannel(InteractionChannel):
    """Voice adapter around the shared runtime. The LiveKit worker feeds it final STT transcripts and
    plays what it emits through TTS; tests/evaluation drive it with text to simulate STT."""

    channel = Channel.VOICE
    modality = OutputModality.SPEECH

    def __init__(self, runtime: AgentRuntime, sessions: SessionManager, session_id: str, tenant_id: str,
                 speak: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.runner = TurnRunner(runtime)
        self._speak = speak
        self._inbox: list[ChannelInput] = []
        self.spoken: list[str] = []
        self.events: list[RuntimeEvent] = []

    def push_transcript(self, text: str, language: str | None = None) -> None:
        self._inbox.append(ChannelInput(text=text, language_hint=language))

    async def receive(self) -> ChannelInput | None:
        return self._inbox.pop(0) if self._inbox else None

    async def send(self, event: RuntimeEvent) -> None:
        self.events.append(event)
        if event.type == "message.completed" and event.response and event.response.text:
            spoken = speech_text(event.response.text, event.response.language)
            self.spoken.append(spoken)
            if self._speak:
                await self._speak(spoken)

    async def interrupt(self) -> None:
        await self.runner.cancel()

    async def handle_utterance(self, transcript: str, language: str | None = None) -> AgentResponse | None:
        state = await self.sessions.get(self.session_id, self.tenant_id)
        if state.channel != Channel.VOICE:
            state = await self.sessions.attach_channel(self.session_id, self.tenant_id, Channel.VOICE)
        req = build_request(state, transcript, channel=Channel.VOICE, modality=OutputModality.SPEECH, language_hint=language)
        return await self.runner.run(req, self.send)
