"""Runs the real LiveKit AgentSession pipeline (text mode: no audio, STT or TTS vendors) with our RuntimeLLM
adapter, proving voice turns flow through LiveKit into the shared AgentRuntime."""

from __future__ import annotations

import pytest

livekit_agents = pytest.importorskip("livekit.agents")

from livekit.agents import AgentSession  # noqa: E402

from app.channels.voice.livekit_agent import (  # noqa: E402
    BFSIVoiceAgent,
    RuntimeLLM,
    VoiceCallContext,
)
from app.domain import Channel  # noqa: E402


def _assistant_texts(result) -> list[str]:
    out = []
    for ev in result.events:
        item = getattr(ev, "item", None)
        if item is not None and getattr(item, "role", None) == "assistant":
            out.append(item.text_content or "")
    return out


async def test_livekit_session_routes_turns_to_shared_runtime(container, authed_session):
    st = await authed_session(Channel.VOICE)
    call = VoiceCallContext(container, st.session_id, st.tenant_id)
    async with AgentSession(llm=RuntimeLLM(call)) as session:
        await session.start(BFSIVoiceAgent(instructions="test"))
        result = await session.run(user_input="Mera credit card ka outstanding kitna hai?")
        spoken = " ".join(_assistant_texts(result))
        assert "23 hazaar 450 rupaye" in spoken and "₹" not in spoken  # speech-rendered for TTS
        result = await session.run(user_input="Transfer ₹100,000 to Rahul")
        assert "one-time password" in " ".join(_assistant_texts(result))
    msgs = await container.memory.all_messages(st.tenant_id, st.conversation_id)
    assert [m.channel for m in msgs if m.role == "user"] == ["voice", "voice"]
    state = await container.sessions.get(st.session_id, st.tenant_id)
    assert state.pending_action and state.pending_action.tool == "transfer_money"
