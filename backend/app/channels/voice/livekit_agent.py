"""LiveKit Agents worker: WebRTC + SIP voice on top of the shared AgentRuntime.

    Audio -> VAD -> turn detection -> streaming STT -> [RuntimeLLM -> AgentRuntime] -> streaming TTS -> LiveKit

LiveKit owns the real-time media concerns (VAD, endpointing, barge-in, partial transcripts, TTS playback).
The "LLM" node handed to LiveKit is `RuntimeLLM`, a thin adapter that forwards the final user transcript
to the very same AgentRuntime the chat API uses — so intent, RAG, tools, policy, auth and handoff logic
are identical across channels. On barge-in LiveKit stops the TTS and cancels the stream; the runtime persists
the partial answer as interrupted, while an account change already on its way to the bank is shielded (never
cancelled) and its outcome is reported on the next turn.

Phone calls arrive through a LiveKit phone number (or a carrier SIP trunk) and a dispatch rule that creates one room
per caller; `VoiceSessionService` admits the call (capacity), binds it to a new session and ends it cleanly.
Each job (= one call) runs in its own process owned by this worker for the call's lifetime (LiveKit dispatch gives
the affinity); session/workflow state lives in Redis/Postgres, so the HTTP API can run on separate workers.

Run:  python -m app.channels.voice.livekit_agent dev        (or `start` in production)
      python -m app.channels.voice.livekit_agent download-files   (turn-detector / VAD model weights)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    NotGivenOr,
    cli,
    llm,
)

from app.channels.base import build_request
from app.channels.voice.audio import create_stt, create_tts, speech_text
from app.channels.voice.session import CallRejected, VoiceCallSession
from app.channels.voice.telephony import ATTR_CALL_STATUS, InboundCall, TelephonyService
from app.channels.voice.vad import load_vad, turn_handling
from app.config import get_settings
from app.container import Container
from app.domain import Channel, OutputModality
from app.escalation.handoff import voice_control_channel
from app.observability import metrics
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing

log = logging.getLogger("bfsi.voice")
_SENTENCE_END = re.compile(r"([.!?।]+[\"')\]]*\s+|\n+)")
FILLERS = {"en": "One moment.", "hi-Latn": "Ek moment.", "hi": "एक क्षण।"}
BUSY = {
    "en": "Sorry, all our lines are busy right now. Please call again in a few minutes. Goodbye.",
    "hi-Latn": "Maaf kijiye, abhi sabhi lines vyast hain. Kripya kuch minute baad dobara call kijiye. Dhanyavaad.",
    "hi": "क्षमा करें, अभी सभी लाइनें व्यस्त हैं। कृपया कुछ मिनट बाद फिर से कॉल करें। धन्यवाद।",
}
GREETINGS = {
    "en": "Hello! You're speaking with {agent} from {bank}. How can I help you today?",
    "hi-Latn": "Namaste! Main {bank} se {agent} bol raha hoon. Bataiye, main aapki kya madad kar sakta hoon?",
    "hi": "नमस्ते! मैं {bank} से {agent} बोल रहा हूँ। बताइए, मैं आपकी क्या मदद कर सकता हूँ?",
}

_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container(get_settings())
    return _container


class VoiceCallContext:
    """Per-call binding between the LiveKit room and the shared session."""

    def __init__(self, container: Container, session_id: str, tenant_id: str) -> None:
        self.c = container
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.language = "en"
        self.language_tag = "en"
        self.turn_started_at: float | None = None
        self.on_language_change: Any = None


class RuntimeLLM(llm.LLM):
    """Presents the AgentRuntime to LiveKit as an `llm.LLM`."""

    def __init__(self, call: VoiceCallContext) -> None:
        super().__init__()
        self.call = call

    @property
    def model(self) -> str:
        return "bfsi-agent-runtime"

    @property
    def provider(self) -> str:
        return "bfsi"

    def chat(self, *, chat_ctx: llm.ChatContext, tools: list[llm.Tool] | None = None,
             conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS, parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
             tool_choice: NotGivenOr[Any] = NOT_GIVEN, extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN) -> RuntimeLLMStream:
        return RuntimeLLMStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class RuntimeLLMStream(llm.LLMStream):
    def __init__(self, runtime_llm: RuntimeLLM, **kw: Any) -> None:
        super().__init__(runtime_llm, **kw)
        self._retry_on_chunk_sent = False  # never replay a banking turn
        self.call = runtime_llm.call

    def _latest_user_text(self) -> str:
        for item in reversed(self._chat_ctx.items):
            if getattr(item, "type", None) == "message" and item.role == "user":
                return item.text_content or ""
        return ""

    def _push(self, chunk_id: str, text: str) -> None:
        if text.strip():
            self._event_ch.send_nowait(llm.ChatChunk(id=chunk_id, delta=llm.ChoiceDelta(role="assistant", content=text)))

    async def _run(self) -> None:
        call = self.call
        text = self._latest_user_text().strip()
        if not text:
            return
        state = await call.c.sessions.get(call.session_id, call.tenant_id)
        req = build_request(state, text, channel=Channel.VOICE, modality=OutputModality.SPEECH)
        metrics.voice_turns.inc()
        buf, spoken_any, filler_sent = "", False, False
        try:
            async for ev in call.c.runtime.stream(req):
                if ev.type == "intent.detected":
                    # language is known before any text streams: render speech (and switch TTS voice) for it now
                    lang = ev.data.get("language") or call.language
                    call.language_tag = ev.data.get("language_tag") or call.language_tag
                    if lang != call.language:
                        call.language = lang
                        if call.on_language_change:
                            call.on_language_change(lang)
                elif ev.type == "message.delta" and ev.content:
                    buf += ev.content
                    # flush complete sentences so TTS starts early but never sees half an amount like "₹1,24"
                    while (m := _SENTENCE_END.search(buf)) is not None:
                        sentence, buf = buf[: m.end()], buf[m.end():]
                        self._push(req.request_id, speech_text(sentence, call.language) + " ")
                        spoken_any = True
                elif ev.type == "workflow.progress" and ev.content and not spoken_any:
                    # deterministic, neutral acknowledgement during slow work ("processing", "still checking") —
                    # never a claim of success; that is only spoken from the verified result
                    self._push(req.request_id, speech_text(ev.content, call.language) + " ")
                    filler_sent = True
                elif ev.type == "tool.started" and not spoken_any and not filler_sent:
                    self._push(req.request_id, FILLERS.get(call.language_tag, FILLERS["en"]) + " ")
                    filler_sent = True
                elif ev.type == "message.completed" and ev.response:
                    if buf.strip():
                        self._push(req.request_id, speech_text(buf, ev.response.language))
                    if ev.response.language != call.language:
                        call.language = ev.response.language
                        if call.on_language_change:
                            call.on_language_change(call.language)
                    st = await call.c.sessions.get(call.session_id, call.tenant_id)
                    call.language_tag = st.response_language_tag
        except asyncio.CancelledError:
            metrics.voice_interruptions.inc()  # barge-in while the runtime was still generating
            raise


class BFSIVoiceAgent(Agent):
    def __init__(self, instructions: str) -> None:
        # Instructions live in the shared runtime's prompts; LiveKit only needs a placeholder here.
        super().__init__(instructions=instructions)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = load_vad()


async def _bootstrap_call(ctx: JobContext, c: Container) -> tuple[VoiceCallSession, rtc.RemoteParticipant | None]:
    """Admit the call and bind it to a session. Raises CallRejected (capacity / unknown number)."""
    admission = await c.voice.admit()
    try:
        meta = json.loads(ctx.job.metadata or "{}")
        if meta.get("session_id") and meta.get("tenant_id"):  # in-app call: token minted for an existing session
            call = await c.voice.start_app_call(meta["session_id"], meta["tenant_id"], room_name=ctx.room.name,
                                                participant_identity=f"customer-{meta['session_id']}", admission=admission)
            return call, None
        # Phone call via the SIP dispatch rule: one new room and one new session per caller
        participant = await ctx.wait_for_participant()
        inbound = InboundCall.from_participant(ctx.room.name, participant.identity, dict(participant.attributes))
        return await c.voice.start_inbound_call(inbound, admission), participant
    except BaseException:
        await c.voice.release(admission)
        raise


async def _reject_call(ctx: JobContext, c: Container, reason: str) -> None:
    """Graceful overflow: transfer to a human queue if configured, else a short message, then hang up."""
    settings = c.settings
    log.warning("call rejected", extra={"reason": reason})
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=5)
    except TimeoutError:
        participant = None
    is_sip = participant is not None and participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    if is_sip and settings.sip_overflow_transfer_uri and reason.startswith("capacity"):
        if await TelephonyService(settings).transfer_to_human(room=ctx.room.name, participant_identity=participant.identity,
                                                              transfer_to=settings.sip_overflow_transfer_uri):
            return
    try:
        tts = create_tts(settings, "en", {})
        session = AgentSession(tts=tts)
        await session.start(agent=BFSIVoiceAgent(instructions="busy"), room=ctx.room)
        await session.say(BUSY["en"], allow_interruptions=False)
        await session.aclose()
    except Exception:  # noqa: BLE001 - no TTS available: just end the call
        log.warning("could not play the busy message")
    if is_sip:
        await TelephonyService(settings).hang_up(ctx.room.name)
    ctx.shutdown(reason=reason)


def _worker_load(server: AgentServer) -> float:
    """MAX_AGENT_SESSIONS_PER_WORKER: LiveKit stops dispatching calls to a worker whose load reaches the threshold."""
    return len(server.active_jobs) / max(1, get_settings().max_agent_sessions_per_worker)


server = AgentServer(setup_fnc=prewarm, load_fnc=_worker_load, load_threshold=1.0)


@server.rtc_session(agent_name=get_settings().livekit_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    setup_tracing(settings.otel_service_name + "-voice", settings.otel_exporter_otlp_endpoint, settings.otel_enabled)
    c = get_container()
    await ctx.connect()
    try:
        voice_call, sip_participant = await _bootstrap_call(ctx, c)
    except CallRejected as e:
        await _reject_call(ctx, c, e.reason)
        return
    session_id, tenant_id = voice_call.session_id, voice_call.tenant_id
    ctx.add_shutdown_callback(lambda: c.voice.end_call(voice_call, reason="session_ended"))  # idempotent safety net
    call = VoiceCallContext(c, session_id, tenant_id)
    state = await c.sessions.get(session_id, tenant_id)
    call.language, call.language_tag = state.language, state.response_language_tag
    profile = await c.directory.get(tenant_id, state.agent_id)
    voice_cfg: dict[str, Any] = profile.voice_config  # per-agent STT/TTS provider and voices

    try:
        tts = create_tts(settings, call.language, voice_cfg)
        stt = create_stt(settings, "multi", voice_cfg)
    except Exception as e:  # missing vendor key / plugin: fail the job loudly, keep the session usable on chat
        log.error("voice providers not configured (set STT_PROVIDER/TTS_PROVIDER and their API keys): %s", e,
                  extra={"session_id": session_id})
        await c.voice.end_call(voice_call, status="failed", reason="voice_providers_not_configured")
        ctx.shutdown(reason="voice providers not configured")
        return
    session = AgentSession(stt=stt, tts=tts, vad=ctx.proc.userdata.get("vad") or load_vad(), llm=RuntimeLLM(call),
                           turn_handling=turn_handling(settings), user_away_timeout=20.0)

    def language_changed(lang: str) -> None:
        if hasattr(tts, "update_options"):
            try:
                from app.channels.voice.audio import SARVAM_LANG

                tts.update_options(target_language_code=SARVAM_LANG.get(lang, "en-IN"))
            except TypeError:
                pass

    call.on_language_change = language_changed

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        kind = type(m).__name__
        if kind == "TTSMetrics" and getattr(m, "ttfb", 0) > 0:
            metrics.tts_latency.observe(m.ttfb)
        elif kind == "EOUMetrics":
            if (d := getattr(m, "transcription_delay", None)) is not None:
                metrics.stt_latency.observe(d)
        elif kind == "LLMMetrics" and getattr(m, "ttft", 0) > 0:
            metrics.first_token_latency.labels("voice").observe(m.ttft)

    @session.on("agent_false_interruption")
    def _on_false_interrupt(_ev) -> None:
        log.info("false interruption resumed")

    @session.on("speech_created")
    def _on_speech(ev) -> None:
        handle = ev.speech_handle
        handle.add_done_callback(lambda h: metrics.voice_interruptions.inc() if h.interrupted else None)

    async def end(status: str, reason: str) -> None:
        # stop media first: an in-progress turn is cancelled (a submitted transfer stays shielded) and releases the
        # session, then the runtime does the end-of-call cleanup
        await session.aclose()
        await c.voice.end_call(voice_call, status=status, reason=reason)

    async def _voice_control() -> None:
        async for cmd in c.store.subscribe(voice_control_channel(session_id)):
            if cmd.get("type") != "transfer":
                continue
            await asyncio.sleep(4)  # let the handoff announcement play
            if sip_participant is not None:
                ok = await TelephonyService(settings).transfer_to_human(room=ctx.room.name, participant_identity=sip_participant.identity)
                log.info("sip transfer", extra={"ok": ok, "handoff_id": cmd.get("handoff_id")})
                if ok:
                    await end("transferred", "transferred_to_human")
            else:  # WebRTC: a human agent joins this room from the desk using the handoff record
                await ctx.room.local_participant.publish_data(json.dumps({"type": "handoff", "handoff_id": cmd.get("handoff_id")}),
                                                              topic="bfsi.events")

    async def _call_timeout() -> None:
        await asyncio.sleep(settings.voice_max_call_seconds)
        await session.say("We've reached the maximum call duration. Thank you for calling. Goodbye!", allow_interruptions=False)
        await asyncio.sleep(3)
        await end("completed", "max_duration")
        if sip_participant is not None:
            await TelephonyService(settings).hang_up(ctx.room.name)

    @ctx.room.on("participant_disconnected")
    def _on_left(p: rtc.RemoteParticipant) -> None:
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:  # a phone hangup is final: end now, no grace period
            asyncio.create_task(end("completed", "caller_hangup"))
            return

        async def grace() -> None:  # allow WebRTC reconnects before ending the call
            await asyncio.sleep(settings.voice_reconnect_grace_seconds)
            if not any(rp.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT for rp in ctx.room.remote_participants.values()):
                await end("completed", "participant_left")
        asyncio.create_task(grace())

    @ctx.room.on("participant_attributes_changed")
    def _on_attrs(changed: dict[str, str], p: rtc.Participant) -> None:
        if changed.get(ATTR_CALL_STATUS) == "hangup":
            asyncio.create_task(end("completed", "caller_hangup"))
    control_task = asyncio.create_task(_voice_control())
    timeout_task = asyncio.create_task(_call_timeout())
    ctx.add_shutdown_callback(lambda: _cancel(control_task, timeout_task))

    await session.start(agent=BFSIVoiceAgent(instructions="Voice front-end for the shared BFSI agent runtime."), room=ctx.room)
    greeting = GREETINGS.get(call.language_tag, GREETINGS["en"]).format(agent=profile.agent_name, bank=profile.tenant_name)
    await session.say(greeting, allow_interruptions=True)


async def _cancel(*tasks: asyncio.Task) -> None:
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    cli.run_app(server)
