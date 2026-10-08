# Architecture

## One runtime, many channels

`AgentRuntime.stream(AgentRequest) -> AsyncIterator[RuntimeEvent]` is the only entry point into business logic.

| Channel | Adapter | What it does |
|---|---|---|
| REST chat | `channels/chat/gateway.py` | builds `AgentRequest(channel=chat, output_modality=text)`, returns the final `AgentResponse` |
| WebSocket chat | `channels/chat/websocket.py` | streams `RuntimeEvent`s as JSON frames; `interrupt` cancels the turn; relays human-agent messages |
| Voice (LiveKit) | `channels/voice/livekit_agent.py` | LiveKit handles VAD, turn detection, STT, barge-in and TTS. Its "LLM" node is `RuntimeLLM`, which forwards the final transcript to the runtime and streams sentence-buffered, speech-rendered text back to TTS |
| Voice (simulated) | `channels/voice/session.py:VoiceChannel` | same adapter without audio — used by tests, the evaluation suite and `POST /voice/simulate` |

`channel` is carried for metrics, audit and policy conditions only; `output_modality` only changes the
style section of the system prompt (spoken answers are short, no markdown). Nothing else branches on channel.

## A turn (`agents/orchestrator.py`)

1. Acquire the **session lock** (renewed Redis lock in production; bounded wait → "busy") — a voice utterance and a chat
   message can never interleave, on one worker or many.
2. Detect language (script + romanised lexicons; Hinglish is `hi` + `Latn`), update the shared session.
3. Strip authentication secrets (OTP/PIN/CVV/password) **before** persistence and before the LLM sees the text.
4. If the session is handed off → relay to the human desk and stop.
5. If an earlier account change has no confirmed outcome → **reconcile**: verify it by idempotency key (never resend)
   and tell the customer.
6. If an OTP challenge is open and the message is a code (`123456`, `123 456`, "one two three…") → verify with the
   institution → resume the held workflow.
7. If a pending action exists → strict confirmation (`yes`/`haan` only; "yes, but ₹50,000" is a *new* request), cancel
   (`no`/`nahi`), or check approval status.
8. **Reason**: intent via LLM structured output (lexicon fallback) + a structured, user-safe reasoning summary.
9. **Tool selection** is intent-scoped: state-changing tools are only offered for `ACTION_REQUEST` / `FRAUD_REQUEST`.
10. Loop (≤ `MAX_AGENT_ITERATIONS`): the LLM proposes one or more tool calls → **Plan** (DAG, templates, plan policy) →
    **Policy** per step in the Tool Gateway → **Act** (independent reads in parallel, mutations alone) → **Verify**
    mutations → structured tool results back to the LLM. See [orchestration.md](orchestration.md).
11. A policy hold (`REQUIRE_AUTH` / `REQUIRE_CONFIRMATION` / `REQUIRE_HUMAN_APPROVAL`) freezes the step as a
    `PendingAction` (exact arguments + context-bound hash, workflow id) and emits a **deterministic** prompt.
12. Grounding check (numbers in the answer must appear in tool output / sources), persist, version-checked session save.

Barge-in: cancelling the stream raises `CancelledError` inside the turn; read steps are cancelled, an in-flight money
movement is shielded and awaited (bounded) before the lock is released; the partial answer is stored with `interrupted=true`.

## Pending-action state machine

```
            tool call ──► Policy Engine
                             │
       ┌──────────── REQUIRE_AUTH ───────────┐
       │   (not identified → ask for mobile;  │
       │    else send OTP: login / transaction│
       │    OTP bound to action hash)         │
       │            OTP verified              │
       ▼                                      │
 REQUIRE_HUMAN_APPROVAL ── checker approves ──┤
       │                                      ▼
       └────────────────────────────► REQUIRE_CONFIRMATION ── "yes" ──► execute frozen args ──► LLM summarises result
                                              │
                                     "no" / new request / TTL expiry ──► cancelled (transaction auth consumed)
```

Every resume re-submits the **same frozen arguments** to the Tool Gateway with an `ActionGrant`
(confirmation flag / approval id bound to the action hash). The LLM is not consulted to re-create the call,
so it cannot change the payee or amount after the customer authenticated or confirmed. The hash covers tool, exact
arguments (amount, payee, resolved beneficiary, currency) and tenant, session and customer.

## Shared session & memory

`SessionState` (`agents/state.py`) holds language, authentication state and factors, intent, pending action,
OTP challenge (challenge id only), handoff status, recent tool results (redacted), RAG context and counters
used by risk scoring. It lives in Redis (idle TTL) and is mirrored to PostgreSQL on every save, so it can be
rehydrated. `POST /voice/session` with a chat session token switches that same session to voice; chat
messages afterwards continue the same `conversation_id`.

## Knowledge (RAG)

Parser (PDF/TXT/MD/HTML) → section-aware chunker (keeps clause headings + page) → metadata (tenant, product,
language, region, validity window, version, status, access level) → embeddings → Elasticsearch
(`text` BM25 with stemming + `dense_vector` cosine kNN). Retrieval runs BM25 and kNN concurrently, fuses with
Reciprocal Rank Fusion, keeps only the newest version per document, reranks (cross-encoder over HTTP or
lexical), and drops low-relevance chunks. The tenant filter is applied inside every store implementation —
callers can only narrow it. Customers never see `internal` documents; new uploads of a document supersede
older versions. Retrieved text is screened for prompt-injection phrasing and defanged.

## Tool Gateway

Every tool — built-in, REST, OpenAPI, MCP or custom adapter — is a `ToolDefinition` with governance fields
(risk level, minimum auth state, confirmation, injected params, intents, internal flag). The model sees the
same `{name, description, input_schema, risk_level}` regardless of transport. `ToolGateway.execute` enforces:
offered-this-turn → strip/inject trusted params (`customer_id` always comes from the session) → JSON-schema
validation → Policy Engine → execution with a per-attempt timeout and retries decided by the tool's trusted execution
metadata (reads; idempotent keyed writes; never financial) → output redaction → `tool_executions` row (attempts,
failure category, workflow/step, idempotency key) + audit event. Scheduling metadata (`operation_type`, `side_effect`,
`parallel_safe`, `idempotent`, `concurrency_group`, `depends_on`) is server-side registration only — see
[orchestration.md](orchestration.md#3-trusted-execution-metadata).

## Data model

`tenants, users, agents, sessions, conversations, conversation_messages, tool_executions, agent_workflows, documents,
document_versions, integrations, mcp_servers, mcp_tools, api_tools, policies, audit_events, handoffs,
approval_requests, evaluation_scenarios, evaluation_runs` — every tenant-owned table carries `tenant_id`;
conversations record the channels used. Bank customers are not stored: only the institution's opaque customer reference.

## Observability

OpenTelemetry spans: `agent.turn` → `llm.stream` / `llm.structured` → `rag.search` → `policy.evaluate` →
`tool.execute`, with W3C `traceparent` propagated to bank APIs and MCP servers. Prometheus metrics (`/metrics`)
are labelled by channel: turn latency, time-to-first-token, LLM latency/tokens, tool latency/outcomes,
RAG latency/result counts, policy decisions, auth events, intents, handoffs, task outcomes, ungrounded answers;
voice-only: STT latency, TTS time-to-first-byte, interruptions, turns, call duration. Logs are JSON with
mandatory PII redaction.
