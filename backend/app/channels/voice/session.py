"""Voice sessions: call lifecycle, LiveKit room + join tokens bound to the shared session, and the VoiceChannel adapter.

    phone (LiveKit phone number -> LiveKit SIP) or app (WebRTC)
            -> LiveKit room (one per call) -> voice worker (media: VAD, turn detection, STT, TTS, barge-in)
            -> VoiceSessionService (this module: admit, start, end a call; bind it to an agent session)
            -> AgentRuntime (reason, plan, policy, execute, verify, respond — shared with chat)

This layer owns *call lifecycle* only. It holds no banking logic: authentication, policy, workflows and what to do
with a half-finished action at hangup are decided by the runtime (`AgentRuntime.end_channel`).
Each call gets its own session_id / conversation_id (phone) or attaches to the app session (WebRTC); nothing about a
call lives in module-level state.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

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
from app.channels.voice.telephony import InboundCall, caller_ref, mask_digits, mask_number, normalize_number, resolve_tenant
from app.config import Settings
from app.database.models import VoiceCall
from app.domain import AgentResponse, AuthMethod, Channel, OutputModality, RuntimeEvent, new_id, utcnow
from app.observability import metrics
from app.sessions.manager import SessionManager

log = logging.getLogger("bfsi.voice")


class CallRejected(RuntimeError):
    """The call cannot be taken (capacity or unknown number). `reason` is safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class CallAdmission:
    """Capacity reserved for one call: an active-call slot plus one STT and one TTS stream (cluster-wide with Redis)."""

    leases: list[tuple[str, str]] = field(default_factory=list)  # (pool, token)


@dataclass
class VoiceCallSession:
    """In-memory handle of one live call, owned by the worker that holds the LiveKit participant."""

    call_id: str
    session_id: str
    tenant_id: str
    transport: str  # sip | webrtc
    room_name: str  # raw LiveKit room (may embed the caller number): memory only, never persisted as-is
    participant_identity: str | None
    started_at: datetime
    admission: CallAdmission
    ended: bool = False


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
    def __init__(self, settings: Settings, sessions: SessionManager, *, db=None, runtime: AgentRuntime | None = None,
                 audit=None, slots=None, auth=None) -> None:
        self.settings = settings
        self.sessions = sessions
        self.db = db
        self.runtime = runtime
        self.audit = audit
        self.slots = slots  # app.agents.execution.concurrency.SlotPool
        self.auth = auth  # CustomerAuthService (caller-id identification only)

    @staticmethod
    def room_for(state: SessionState) -> str:
        return f"bfsi-{state.tenant_id[:8]}-{state.session_id}"

    # ------------------------------------------------------------------------------------------ app (WebRTC) join
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

    # ------------------------------------------------------------------------------------------ call lifecycle
    async def admit(self) -> CallAdmission:
        """Reserve capacity for a new call or raise CallRejected — never accept a call we cannot serve."""
        s = self.settings
        admission = CallAdmission()
        if self.slots is None:
            return admission
        ttl = s.voice_max_call_seconds + 120  # outlives the longest call; a crashed worker's lease expires on its own
        for pool, limit in (("calls", s.max_active_calls), ("stt", s.max_concurrent_stt_requests),
                            ("tts", s.max_concurrent_tts_requests)):
            token = await self.slots.try_acquire(pool, limit, ttl=ttl)
            if token is None:
                await self.release(admission)
                metrics.voice_calls.labels("rejected", pool).inc()
                raise CallRejected(f"capacity:{pool}")
            admission.leases.append((pool, token))
        return admission

    async def release(self, admission: CallAdmission) -> None:
        if self.slots is None:
            return
        for pool, token in admission.leases:
            try:
                await self.slots.release(pool, token)
            except Exception:  # noqa: BLE001 - the lease expires anyway
                log.warning("could not release call capacity lease", extra={"pool": pool})
        admission.leases.clear()

    async def start_inbound_call(self, call: InboundCall, admission: CallAdmission) -> VoiceCallSession:
        """Inbound phone call: a NEW session per call (never keyed by phone number). The caller's number may
        *identify* a candidate customer; authentication still happens in the runtime (OTP etc.)."""
        assert self.db is not None and self.auth is not None
        tenant = await resolve_tenant(self.db, call)
        if tenant is None:
            metrics.voice_calls.labels("rejected", "unknown_number").inc()
            raise CallRejected("unknown_number")
        state = await self.sessions.create(tenant_id=tenant.id, channel=Channel.VOICE, language=tenant.default_language)
        key = self.settings.caller_id_hash_key or self.settings.jwt_secret
        row = VoiceCall(
            id=new_id(), tenant_id=tenant.id, session_id=state.session_id, conversation_id=state.conversation_id,
            transport="sip", direction="inbound", room_name=mask_digits(call.room_name),
            participant_identity=mask_digits(call.participant_identity), sip_call_id=call.sip_call_id,
            sip_trunk_id=call.trunk_id, sip_rule_id=call.rule_id, dialed_number=normalize_number(call.dialed_number),
            caller_number_masked=mask_number(call.caller_number), caller_ref=caller_ref(call.caller_number, key),
            status="active", worker_id=self.settings.worker_id, started_at=utcnow())
        async with self.db.session() as s:
            s.add(row)
        try:
            async with self.sessions.locked(state.session_id, tenant.id) as st:
                st.call_id = row.id
                digits = "".join(ch for ch in (call.caller_number or "") if ch.isdigit())
                if len(digits) >= 10:
                    # Caller-id only IDENTIFIES the customer (IDENTIFIED); it is never treated as authentication.
                    await self.auth.identify(st, phone=digits[-10:], method=AuthMethod.CALLER_ID)
        except BaseException:
            async with self.db.session() as s:
                if (r := await s.get(VoiceCall, row.id)) is not None:
                    r.status, r.end_reason, r.ended_at = "failed", "setup_failed", utcnow()
            raise
        await self._audit(tenant.id, "call.started", state.session_id, {
            "call_id": row.id, "transport": "sip", "direction": "inbound", "caller_ref": row.caller_ref,
            "dialed_number": row.dialed_number, "sip_call_id": call.sip_call_id})
        metrics.voice_calls.labels("started", "sip").inc()
        log.info("call started", extra={"call_id": row.id, "session_id": state.session_id, "transport": "sip"})
        return VoiceCallSession(call_id=row.id, session_id=state.session_id, tenant_id=tenant.id, transport="sip",
                                room_name=call.room_name, participant_identity=call.participant_identity,
                                started_at=row.started_at, admission=admission)

    async def start_app_call(self, session_id: str, tenant_id: str, *, room_name: str, participant_identity: str | None,
                             admission: CallAdmission) -> VoiceCallSession:
        """In-app (WebRTC) call: voice attaches to the customer's existing session and keeps its authentication."""
        state = await self.sessions.attach_channel(session_id, tenant_id, Channel.VOICE)
        row = VoiceCall(id=new_id(), tenant_id=tenant_id, session_id=session_id, conversation_id=state.conversation_id,
                        transport="webrtc", direction="inbound", room_name=mask_digits(room_name),
                        participant_identity=participant_identity, status="active", worker_id=self.settings.worker_id,
                        started_at=utcnow())
        if self.db is not None:
            async with self.db.session() as s:
                s.add(row)
        async with self.sessions.locked(session_id, tenant_id) as st:
            st.call_id = row.id
        await self._audit(tenant_id, "call.started", session_id, {"call_id": row.id, "transport": "webrtc"})
        metrics.voice_calls.labels("started", "webrtc").inc()
        return VoiceCallSession(call_id=row.id, session_id=session_id, tenant_id=tenant_id, transport="webrtc",
                                room_name=room_name, participant_identity=participant_identity,
                                started_at=row.started_at, admission=admission)

    async def end_call(self, call: VoiceCallSession, *, status: str = "completed", reason: str = "hangup") -> dict:
        """Hangup / disconnect / transfer. Idempotent. What happens to unfinished work is the runtime's decision:
        unsubmitted holds are cancelled, submitted money movements are never cancelled (verified, or escalated)."""
        if call.ended:
            return {}
        call.ended = True
        outcome: dict = {}
        try:
            if self.runtime is not None:
                outcome = await self.runtime.end_channel(call.session_id, call.tenant_id, channel=Channel.VOICE,
                                                         close=call.transport == "sip" and status != "transferred", reason=reason)
        except Exception:
            log.exception("end-of-call cleanup failed", extra={"call_id": call.call_id})
            status = "failed"
        finally:
            ended = utcnow()
            duration = (ended - call.started_at).total_seconds()
            if self.db is not None:
                async with self.db.session() as s:
                    row = await s.get(VoiceCall, call.call_id)
                    if row is not None:
                        row.status, row.end_reason, row.ended_at = status, reason[:64], ended
                        row.duration_seconds = round(duration, 1)
            await self.release(call.admission)
            metrics.voice_calls.labels("ended", status).inc()
            metrics.call_duration.observe(duration)
        await self._audit(call.tenant_id, "call.ended", call.session_id, {
            "call_id": call.call_id, "status": status, "reason": reason, "duration_seconds": round(duration, 1),
            **{k: v for k, v in outcome.items() if k in ("pending_cancelled", "unconfirmed", "handoff_id", "closed")}})
        log.info("call ended", extra={"call_id": call.call_id, "session_id": call.session_id, "status": status, "reason": reason})
        return outcome

    async def _audit(self, tenant_id: str, event: str, session_id: str, payload: dict) -> None:
        if self.audit is not None:
            await self.audit.record(tenant_id, event, actor_type="system", session_id=session_id, channel="voice",
                                    outcome="success", payload=payload)


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
