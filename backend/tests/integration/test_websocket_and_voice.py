from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.channels.voice.session import VoiceChannel, livekit_token
from app.domain import AgentRequest, Channel, OutputModality
from mock_bank import data as bank_data
from mock_bank.app import app as bank_app
from tests.conftest import customer_assertion, make_settings


def test_websocket_streaming_events(tmp_path):
    from app.container import Container
    from app.main import create_app

    bank_data.reset()
    settings = make_settings(tmp_path).model_copy(update={"auto_seed": True})
    app = create_app(settings, container_factory=lambda s: Container(s, http_transport=httpx.ASGITransport(app=bank_app)))
    with TestClient(app) as client:
        r = client.post("/sessions", json={"tenant": "demo-bank", "customer_assertion": customer_assertion()})
        sid, token = r.json()["session"]["session_id"], r.json()["session_token"]
        with client.websocket_connect(f"/ws/chat/{sid}?token={token}") as ws:
            assert ws.receive_json()["type"] == "session.ready"

            def turn(frame):
                ws.send_json(frame)
                events = []
                while True:
                    ev = ws.receive_json()
                    events.append(ev)
                    if ev["type"] == "typing" and ev["state"] == "stopped":
                        return events

            evs = turn({"type": "message", "content": "What is my loan balance?"})
            types = [e["type"] for e in evs]
            for t in ("typing", "processing.started", "intent.detected", "tool.started", "tool.completed", "message.delta", "message.completed"):
                assert t in types
            assert next(e for e in evs if e["type"] == "tool.started")["tool"] == "get_loan_details"
            assert "28,45,000" in next(e for e in evs if e["type"] == "message.completed")["response"]["text"]

            evs = turn({"type": "message", "content": "Transfer ₹10,000 to Rahul"})
            assert any(e["type"] == "auth.required" for e in evs)
            evs = turn({"type": "auth.otp", "otp": "123456"})
            conf = next(e for e in evs if e["type"] == "confirmation.required")
            assert "₹10,000" in conf["data"]["summary"]
            evs = turn({"type": "message", "content": "yes"})
            assert "successful" in next(e for e in evs if e["type"] == "message.completed")["response"]["text"].lower()
        with client.websocket_connect(f"/ws/chat/{sid}?token=bad") as bad, pytest.raises(WebSocketDisconnect) as exc:
            bad.receive_json()
        assert exc.value.code == 1008
        msgs = client.get(f"/sessions/{sid}/messages", headers={"Authorization": f"Bearer {token}"}).json()
        assert "123456" not in str(msgs)


async def test_voice_channel_spoken_otp_and_tts_rendering(container, authed_session):
    st = await authed_session(Channel.VOICE)
    v = VoiceChannel(container.runtime, container.sessions, st.session_id, st.tenant_id)
    r = await v.handle_utterance("Transfer ₹100,000 to Rahul")
    assert r.pending_action.decision.value == "REQUIRE_AUTH"
    r = await v.handle_utterance("one two three four five six")  # spoken digits from STT
    assert r.pending_action.decision.value == "REQUIRE_CONFIRMATION"
    assert "one lakh rupees" in v.spoken[-1] and "₹" not in v.spoken[-1]
    r = await v.handle_utterance("haan ji")
    assert "successful" in r.text.lower()
    msgs = await container.memory.all_messages(st.tenant_id, st.conversation_id)
    assert all("one two three four five six" not in m.content for m in msgs)


async def test_barge_in_cancels_turn_and_persists_partial(container, authed_session):
    st = await authed_session(Channel.VOICE)
    slow_started = asyncio.Event()
    orig = container.rest_executor.execute

    async def slow(*a, **kw):
        slow_started.set()
        await asyncio.sleep(5)
        return await orig(*a, **kw)

    container.rest_executor.execute = slow
    v = VoiceChannel(container.runtime, container.sessions, st.session_id, st.tenant_id)
    task = asyncio.create_task(v.handle_utterance("What is my loan balance?"))
    await asyncio.wait_for(slow_started.wait(), 5)
    await v.interrupt()
    assert await task is None
    container.rest_executor.execute = orig
    msgs = await container.memory.all_messages(st.tenant_id, st.conversation_id)
    assert msgs[-1].role == "assistant" and msgs[-1].interrupted
    # the session lock was released: the next turn works
    r = await container.runtime.process(AgentRequest(tenant_id=st.tenant_id, session_id=st.session_id, channel=Channel.VOICE,
                                                     message="What is my loan balance?", output_modality=OutputModality.SPEECH))
    assert "28,45,000" in r.text


def test_livekit_token_claims():
    import jwt

    tok = livekit_token(api_key="k", api_secret="s" * 32, identity="customer-1", room="r1", agent_name="bfsi-voice-agent",
                        agent_metadata={"session_id": "1"})
    c = jwt.decode(tok, "s" * 32, algorithms=["HS256"])
    assert c["iss"] == "k" and c["video"]["room"] == "r1" and c["video"]["roomJoin"]
    assert c["roomConfig"]["agents"][0]["agentName"] == "bfsi-voice-agent"
