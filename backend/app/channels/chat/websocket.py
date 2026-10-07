"""WebSocket chat channel: streaming events, typing state, interrupts, human-agent messages.

Client -> server frames:
  {"type": "message", "content": "...", "language": "hi"?}
  {"type": "auth.otp", "otp": "123456"}          # OTP never shown in transcript, never sent to the LLM
  {"type": "interrupt"}                          # cancel the in-flight answer
  {"type": "ping"}
Server -> client frames: RuntimeEvent JSON (`message.delta`, `tool.started`, `tool.completed`,
`auth.required`, `confirmation.required`, `handoff.initiated`, `message.completed`, `error`, ...),
plus `typing`, `human.message`, `handoff.accepted`, `handoff.resolved`, `pong`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.channels.base import (
    ChannelInput,
    InteractionChannel,
    TurnRunner,
    build_request,
)
from app.domain import Channel, OutputModality, RuntimeEvent
from app.escalation.handoff import session_events_channel

log = logging.getLogger(__name__)
MAX_MESSAGE_CHARS = 4000


class WebSocketChatChannel(InteractionChannel):
    channel = Channel.CHAT
    modality = OutputModality.TEXT

    def __init__(self, ws: WebSocket, container, session_id: str, tenant_id: str) -> None:
        self.ws = ws
        self.c = container
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.runner = TurnRunner(container.runtime)
        self._send_lock = asyncio.Lock()

    async def receive(self) -> ChannelInput | None:
        while True:
            try:
                raw = await self.ws.receive_text()
            except WebSocketDisconnect:
                return None
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await self._send_json({"type": "error", "content": "invalid JSON frame"})
                continue
            kind = frame.get("type")
            if kind == "ping":
                await self._send_json({"type": "pong"})
            elif kind == "interrupt":
                await self.interrupt()
            elif kind == "auth.otp":
                return ChannelInput(text=str(frame.get("otp", ""))[:12], metadata={"input": "otp"})
            elif kind == "message":
                text = str(frame.get("content", "")).strip()
                if not text:
                    continue
                if len(text) > MAX_MESSAGE_CHARS:
                    await self._send_json({"type": "error", "content": "message too long"})
                    continue
                return ChannelInput(text=text, language_hint=frame.get("language"))
            else:
                await self._send_json({"type": "error", "content": f"unknown frame type {kind!r}"})

    async def send(self, event: RuntimeEvent) -> None:
        await self._send_json(event.model_dump(mode="json", exclude_none=True))

    async def interrupt(self) -> None:
        if await self.runner.cancel():
            await self._send_json({"type": "message.interrupted"})

    async def _send_json(self, data: dict) -> None:
        async with self._send_lock:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await self.ws.send_json(data)

    async def _relay_session_events(self) -> None:
        async for ev in self.c.store.subscribe(session_events_channel(self.session_id)):
            await self._send_json(ev)

    async def serve(self) -> None:
        state = await self.c.sessions.get(self.session_id, self.tenant_id)
        if state.channel != Channel.CHAT:
            state = await self.c.sessions.attach_channel(self.session_id, self.tenant_id, Channel.CHAT)
        await self._send_json({"type": "session.ready", "session": state.public_view()})
        relay = asyncio.create_task(self._relay_session_events())
        turn: asyncio.Task | None = None
        try:
            while True:
                inp = await self.receive()
                if inp is None:
                    break
                if turn and not turn.done():  # a new message barges in on the previous answer
                    await self.interrupt()
                state = await self.c.sessions.get(self.session_id, self.tenant_id)
                req = build_request(state, inp.text, channel=Channel.CHAT, modality=self.modality,
                                    language_hint=inp.language_hint, metadata=inp.metadata)
                await self._send_json({"type": "typing", "state": "started"})
                turn = asyncio.create_task(self._run_turn(req))
        finally:
            relay.cancel()
            await self.runner.cancel()

    async def _run_turn(self, req) -> None:
        try:
            await self.runner.run(req, self.send)
        except Exception:
            log.exception("websocket turn failed")
            await self._send_json({"type": "error", "content": "turn failed"})
        finally:
            await self._send_json({"type": "typing", "state": "stopped"})
