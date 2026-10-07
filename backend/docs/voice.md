# Voice

```
Browser (WebRTC) / Phone (SIP trunk → LiveKit SIP)
        │
     LiveKit room ── agent dispatch (token roomConfig or SIP dispatch rule) ──► voice worker
        │                                                                     (app/channels/voice/livekit_agent.py)
  Silero VAD → multilingual turn detector → streaming STT → RuntimeLLM → AgentRuntime (shared with chat)
        ▲                                                                       │
        └──────────────────────── streaming TTS ◄── sentence-buffered speech text┘
```

## Running

```bash
docker compose --profile voice up -d          # LiveKit dev server (devkey/secret) + worker
# or locally:
python -m app.channels.voice.livekit_agent download-files   # turn-detector + VAD weights (once)
python -m app.channels.voice.livekit_agent dev
```

Client flow: `POST /sessions` (or reuse a chat session) → `POST /voice/session` with the session token →
connect to `livekit_url` with `participant_token`. Joining dispatches the `bfsi-voice-agent` worker, which
attaches voice to the same session/conversation. `POST /voice/token` re-issues a token for reconnects.

Phone: configure a LiveKit SIP inbound trunk + dispatch rule with agent name `bfsi-voice-agent`, and list the
institution's numbers in `tenant.settings.sip_numbers`. The caller's number only **identifies** the customer;
authentication still needs OTP (spoken digits are understood and never stored) or another strong factor.

## Providers (independently configurable)

| Concern | Setting | Options |
|---|---|---|
| STT | `STT_PROVIDER` | `deepgram` (nova-3 `multi`, handles Hindi–English code-switching), `sarvam` (Indic-first), `openai` |
| TTS | `TTS_PROVIDER` | `sarvam` (Indic voices), `elevenlabs`, `openai` |
| Turn detection | — | LiveKit multilingual end-of-utterance model, falls back to VAD endpointing |
| LLM | `LLM_PROVIDER` | shared with chat |

Per-agent overrides live in `agents.voice_config` (`stt_provider`, `tts_provider`, `voices: {lang: voice}`).

## Behaviour

* **Barge-in**: interruption enabled (min 0.5 s speech, false-interruption resume). LiveKit cancels the runtime
  stream; the partial answer is stored as interrupted; the next utterance is processed normally.
* **No preemptive generation**: the runtime never starts tool calls on partial transcripts.
* **Latency masking**: a short localized filler ("One moment." / "Ek moment.") is spoken when a tool call
  starts before any answer text.
* **Speech rendering**: `₹1,00,000` → "one lakh rupees" / "1 lakh rupaye", masked numbers → "ending in 4 2 4 2",
  citations and markdown removed. Text is flushed to TTS per sentence so amounts are never split.
* **Language**: detected each turn and announced before any text streams, so speech rendering follows the
  customer's language immediately. Mid-call TTS voice switching is wired for Sarvam (`update_options`); other
  TTS providers keep the voice chosen at call start.
* **Handoff**: the runtime publishes a `transfer` command; SIP callers are transferred with SIP REFER to
  `SIP_HUMAN_TRANSFER_URI`, WebRTC rooms get a `handoff` data message so a human can join with the handoff context.
* **Timeouts**: max call duration (`VOICE_MAX_CALL_SECONDS`), reconnect grace (`VOICE_RECONNECT_GRACE_SECONDS`).

## Metrics

`bfsi_voice_stt_latency_seconds`, `bfsi_voice_tts_ttfb_seconds`, `bfsi_time_to_first_token_seconds{channel="voice"}`,
`bfsi_turn_latency_seconds{channel="voice"}`, `bfsi_voice_interruptions_total`, `bfsi_voice_turns_total`,
`bfsi_voice_call_duration_seconds`. These are recorded inside the worker's job processes; serving them is not
wired yet (needs Prometheus multiprocess mode or an OTLP metrics exporter). Traces already flow via OTLP.
