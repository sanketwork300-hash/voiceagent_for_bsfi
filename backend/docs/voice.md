# Voice & telephony

LiveKit is the real-time phone/media layer. It does **not** decide anything about banking: whether an action is
allowed, whether tools run in parallel, whether authentication is sufficient, whether a confirmation is valid or
whether a transaction succeeded are decided by the Agent Runtime, Policy Engine, execution DAG and verification —
the same code that serves chat ([orchestration.md](orchestration.md)). There is no separate phone decision engine
and no third-party PSTN provider in the application.

```
 PSTN caller ─► LiveKit Phone Number  (or a carrier SIP trunk into LiveKit SIP)
                     │
                LiveKit SIP ── dispatch rule: one *individual* room per caller, voice agent dispatched,
                     │                         participant attribute bfsi.tenant=<slug>
 App (WebRTC) ─► LiveKit room (one per call)
                     │
                Voice agent worker  (app/channels/voice/livekit_agent.py — owns the media connection for the call)
                  Silero VAD · multilingual turn detector · streaming STT · streaming TTS · barge-in
                     │  RuntimeLLM adapter (final transcript in, sentence-buffered speech text out)
                VoiceSessionService (app/channels/voice/session.py — call lifecycle only)
                     │
                AgentRuntime ─► Reason ─► Plan ─► Policy ─► Execution DAG (parallel READs │ sequential WRITEs)
                     │                                       ─► Verify ─► Respond
                LiveKit TTS ─► caller

 Shared: Redis (session state, per-session locks, capacity leases)  ·  PostgreSQL (sessions, workflows, voice_calls, audit)
```

| Layer | Owns | Never owns |
|---|---|---|
| LiveKit (phone number, SIP, room) | PSTN/SIP ingress, media, per-call rooms, agent dispatch | anything about money |
| Voice worker (`livekit_agent.py`) | VAD, turn detection, STT, TTS, barge-in, filler/acks, hangup detection | policy, auth, workflows |
| `VoiceSessionService` | admit / start / end a call, bind it to a session, `voice_calls` record | business decisions |
| Agent Runtime | reasoning, planning, policy, execution, verification, what happens to unfinished work at hangup | media |

## Phone numbers: setup

1. **Get a number.** Buy a LiveKit Phone Number in LiveKit Cloud (dashboard or `lk` CLI). Check which countries are
   available; where LiveKit does not sell numbers (e.g. for Indian DIDs), bring a carrier SIP trunk into LiveKit SIP
   and create an inbound trunk for it — everything below is identical.
2. **Bind it to the agent** (creates/updates the dispatch rule; idempotent):

   ```bash
   python -m scripts.provision_telephony --tenant demo-bank --number +14155550123
   ```

   The rule gives every caller their own room (`LIVEKIT_SIP_ROOM_PREFIX`), dispatches `LIVEKIT_AGENT_NAME`, and sets
   `bfsi.tenant=<slug>` on the caller's SIP participant (server-side configuration — the caller cannot influence it).
   The numbers are also recorded in `tenant.settings.sip_numbers` as a fallback for resolving the institution by
   dialled number (`sip.trunkPhoneNumber`).
3. Run the voice worker (`python -m app.channels.voice.livekit_agent start`) with STT/TTS keys.

Configuration (all from the environment — no keys, secrets, numbers or SIP credentials in code):

| Setting | |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit server / Cloud project (worker, provisioning, SIP API) |
| `LIVEKIT_PUBLIC_URL` | URL handed to apps/browsers (defaults to `LIVEKIT_URL`) |
| `LIVEKIT_AGENT_NAME` | agent dispatched by tokens and the dispatch rule (`bfsi-voice-agent`) |
| `LIVEKIT_SIP_ROOM_PREFIX`, `LIVEKIT_SIP_DISPATCH_RULE_NAME` | dispatch-rule room prefix / name |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | outbound callbacks only (formerly `SIP_TRUNK_ID`, still accepted) |
| `SIP_HUMAN_TRANSFER_URI`, `SIP_OVERFLOW_TRANSFER_URI` | SIP REFER targets for human handoff / all-lines-busy |
| `CALLER_ID_HASH_KEY` | HMAC key for the internal caller reference |
| `STT_PROVIDER`, `TTS_PROVIDER` + vendor keys | speech providers (below) |
| `VOICE_MAX_CALL_SECONDS`, `VOICE_RECONNECT_GRACE_SECONDS` | call limit; WebRTC reconnect grace (phone hangups end immediately) |

## Call lifecycle

```
INBOUND CALL ─► LiveKit SIP accepts ─► dispatch rule creates room "call-…" ─► worker job (one process per call)
  ─► VoiceSessionService.admit()        reserve an active-call slot + one STT and one TTS stream   (else: busy path)
  ─► start_inbound_call()               resolve institution (bfsi.tenant / dialled number)
                                        NEW session_id + conversation_id for this call (never keyed by phone number)
                                        voice_calls row (masked number, caller_ref) · audit call.started
                                        caller-id lookup -> candidate customer -> IDENTIFIED (not authenticated)
  ─► greeting ─► caller speaks ─► VAD / turn detection / STT ─► AgentRuntime (Reason → Plan → Policy → DAG → Verify → Respond)
  ─► sentence-buffered TTS ─► caller hears the answer
CALL TERMINATED (SIP participant left / sip.callStatus=hangup / max duration / transfer)
  ─► VoiceSessionService.end_call() ─► AgentRuntime.end_channel():
        in-flight money movements are awaited, never cancelled
        an action still waiting for OTP / confirmation / approval was never submitted -> cancelled (workflow CANCELLED)
        OTP challenge and single-use transaction authentication discarded
        an unconfirmed outcome is verified now; still unknown -> human follow-up (handoff) and the session stays open
        phone sessions are closed; app sessions just detach voice
  ─► voice_calls row: ended_at, duration, status, end_reason · audit call.ended · capacity released
```

Every call — phone or app — has its own `session_id`, `conversation_id` and per-turn `workflow_id`s. Identity,
authentication, OTP challenge, transaction authentication, pending action, history, workflows, tool results, RAG
context, fraud flags and call state all live in that session (Redis + Postgres), never in process-global state;
`tests/integration/test_telephony.py` runs three simultaneous callers on one number (two of them the same customer)
and checks they stay isolated.

### Call metadata (`voice_calls`)

`session_id, conversation_id, tenant_id, transport (sip|webrtc), direction, room_name, participant_identity,
sip_call_id, sip_trunk_id, sip_rule_id, dialed_number, caller_number_masked, caller_ref, status
(active|completed|transferred|rejected|failed), end_reason, worker_id, started_at, ended_at, duration_seconds`.

Phone-number hygiene: the raw caller number is read from the SIP participant once, used for the customer lookup and
otherwise dropped. Persisted and logged forms are masked (`XXXXXX3210`; room names and `sip_+91…` identities have
their digit runs masked) plus a keyed hash (`caller_ref`) for correlating repeat callers. LiveKit embeds the number in
individual room names, so the raw room name is kept only in the worker's memory for the call (SIP transfer, hangup).
The existing redaction filters still apply to logs and tool-execution records.

## Caller identity and authentication

```
caller number ─► lookup_customer ─► candidate customer ─► IDENTIFIED (caller_id) ─► OTP / bank IdP ─► authenticated session
```

A known number is never authentication. *"What's my balance?"* still needs the normal login step (an OTP to the
registered mobile; spoken digits are understood and never stored). *"Transfer ₹50,000 to Rahul"* still needs
authentication → transaction OTP bound to that exact transfer → explicit confirmation → policy → execution →
verification. Voice-biometric matches (if wired) only reach `PARTIALLY_AUTHENTICATED`.

## Barge-in, "wait!", long operations

* **Barge-in**: LiveKit stops TTS and cancels the response stream; the old answer never keeps playing. The runtime
  stores the partial answer as interrupted and processes the new utterance. A money movement that was already sent is
  shielded: the turn waits (bounded) for it, its result is recorded, and if the caller never heard it, the next turn
  starts with *"An update on your earlier request … the bank has confirmed it was completed (reference …)."*
* **"Wait!" / "stop!" after submission**: answered deterministically from the workflow state — *"That request had
  already been sent to the bank, and the bank has confirmed it was completed… I can't reverse it from here"*, or
  *"…I couldn't confirm yet whether it completed; I'm checking its status and I won't send it again."* No reversal is
  attempted because a voice response was interrupted (audited `action.stop_requested_after_submission`). Before
  submission, "no/stop/cancel" simply cancels the pending action.
* **Slow operations**: neutral acknowledgements only — *"I'll check the details and prepare that for you."* when an
  account change is planned, *"Processing your request now. I'll confirm as soon as the bank responds."* once it is
  sent, *"Still checking…"* for slow reads, *"Checking the final status with the bank."* while verifying an ambiguous
  result. *"The transfer was completed"* is only ever spoken from a verified result; an unknown outcome is *"I couldn't
  confirm whether … went through. I have not sent it again … I'm checking its status …"* with a human follow-up.

## Scaling

Voice capacity is not just "more backend workers". Each dimension is limited and fails gracefully:

| Dimension | Limit | Behaviour at the limit |
|---|---|---|
| PSTN / SIP | LiveKit phone-number / trunk channel capacity (LiveKit Cloud plan / carrier) | carrier/LiveKit rejects the call |
| Active calls | `MAX_ACTIVE_CALLS` — cluster-wide lease in Redis | caller hears "all lines are busy" (or is transferred to `SIP_OVERFLOW_TRANSFER_URI`), call ends |
| Agent sessions per worker | `MAX_AGENT_SESSIONS_PER_WORKER` — reported to LiveKit as worker load (`load_fnc`, threshold 1.0) | LiveKit dispatches new calls to other workers |
| STT streams | `MAX_CONCURRENT_STT_REQUESTS` — one stream reserved per call (cluster-wide) | busy path |
| TTS streams | `MAX_CONCURRENT_TTS_REQUESTS` — one stream reserved per call (at most one utterance plays per call) | busy path |
| LLM | `MAX_CONCURRENT_LLM_REQUESTS` — cluster-wide lease per request, `LLM_QUEUE_TIMEOUT` | fails over to `LLM_FALLBACK_PROVIDER` (or a polite error) — never an unbounded queue |
| Tools / bank APIs | `MAX_CONCURRENT_TOOLS` (= `TOOL_GLOBAL_CONCURRENCY`, per process), `TOOL_GROUP_LIMITS` (+ `DISTRIBUTED_TOOL_LIMITS`) | step waits up to its deadline, then fails "busy, nothing was sent" |
| Redis / Postgres | connection pools, Redis memory/ops; sessions and leases expire | standard capacity planning |

Without Redis (dev) the pools are per process. Calls never need one backend process or one LLM instance per caller:
the runtime is async and shares bounded pools.

**Session affinity.** A LiveKit job is the call: LiveKit dispatches it to exactly one worker, which keeps the media
connection for the call's lifetime (each job runs in its own process inside the worker). That is the affinity — HTTP
load balancing does not apply to it. Run the realtime workload (`voice-agent` service: `livekit_agent start`) separately
from the HTTP/API workload (`backend` service); both share Redis and Postgres, so any API worker can serve the same
session (e.g. the customer continues in chat) and a turn can never interleave with another (per-session Redis lock).

## Providers (independently configurable)

| Concern | Setting | Options |
|---|---|---|
| STT | `STT_PROVIDER` | `deepgram` (nova-3 `multi`, handles Hindi–English code-switching), `sarvam` (Indic-first), `openai` |
| TTS | `TTS_PROVIDER` | `sarvam` (Indic voices), `elevenlabs`, `openai` |
| Turn detection | — | LiveKit multilingual end-of-utterance model, falls back to VAD endpointing |
| LLM | `LLM_PROVIDER` | shared with chat |

Per-agent overrides live in `agents.voice_config` (`stt_provider`, `tts_provider`, `voices: {lang: voice}`).

## Running locally

```bash
docker compose --profile voice up -d          # LiveKit dev server (devkey/secret) + worker
python -m app.channels.voice.livekit_agent download-files   # turn-detector + VAD weights (once)
python -m app.channels.voice.livekit_agent dev
```

App flow: `POST /sessions` (or reuse a chat session) → `POST /voice/session` → connect to `livekit_url` with
`participant_token`; joining dispatches the worker, which attaches voice to the same session. `POST /voice/simulate`
runs a voice turn from text (dev only). The local LiveKit dev server has no PSTN; phone calls need LiveKit Cloud
phone numbers or a SIP trunk.

## Other behaviour

* **Speech rendering**: `₹1,00,000` → "one lakh rupees" / "1 lakh rupaye", masked numbers → "ending in 4 2 4 2",
  citations and markdown removed; text is flushed to TTS per sentence so amounts are never split.
* **Language**: detected each turn and announced before any text streams; mid-call voice switching wired for Sarvam.
* **Handoff**: SIP callers are transferred with SIP REFER to `SIP_HUMAN_TRANSFER_URI` (call record `transferred`);
  WebRTC rooms get a `handoff` data message so a human can join with the handoff context.

## Metrics

`bfsi_voice_calls_total{event=started|rejected|ended}`, `bfsi_voice_call_duration_seconds`,
`bfsi_voice_stt_latency_seconds`, `bfsi_voice_tts_ttfb_seconds`, `bfsi_time_to_first_token_seconds{channel="voice"}`,
`bfsi_turn_latency_seconds{channel="voice"}`, `bfsi_voice_interruptions_total`, `bfsi_voice_turns_total`,
`bfsi_concurrency_limit_hits_total{scope=llm|…}`. Recorded inside the worker's job processes; exporting them needs
Prometheus multiprocess mode or an OTLP metrics exporter (not wired yet). Traces flow via OTLP.
