# BFSI Agent Platform — Chat + Voice Backend

Multi-tenant backend for AI agents serving banks, NBFCs, insurers, fintechs and payment companies.
**One Agent Runtime serves both chat and voice**: the channels are thin adapters; intent, RAG, tools,
policy, authentication, PII protection, handoff and audit are shared code.

```
 Chat (REST / WebSocket)            Voice (WebRTC / SIP via LiveKit: VAD → turn detection → STT … TTS)
            │                                      │  RuntimeLLM adapter
            └──────────────┬───────────────────────┘
                           ▼
                 AgentRuntime / Orchestrator  ── shared session + conversation state (Redis + Postgres)
                  Reason → Plan → Policy → Act → Verify → Respond · pending-action state machine
                  execution engine: DAG of tool steps · parallel reads · serialized, verified money movement
                           │
        ┌──────────────────┼──────────────────────┐
   search_knowledge     Tool Gateway  ───────►  Policy Engine (auth level + factor strength,
   RAG (Elasticsearch   (REST · OpenAPI ·         risk, maker-checker, confirmation)
   BM25+kNN+rerank)      MCP · adapters)               │
                           └──────────► Bank / NBFC APIs & MCP servers (system of record)
```

The platform never touches an institution's database: customer data and operations go through REST,
OpenAPI-described APIs, MCP servers or registered adapters. The LLM proposes tool calls; only the policy
engine can authorise them, and the execution engine — not the model — decides what runs in parallel, what waits, and
whether an action really succeeded ([docs/orchestration.md](docs/orchestration.md)).

## Quickstart (Docker)

```bash
cp .env.example .env
docker compose up -d --build --wait      # backend :8000, mock bank :8100, Postgres, Redis, Elasticsearch
pip install -e . && python -m scripts.demo   # end-to-end prototype walkthrough against the running stack
```

`AUTO_SEED=true` creates the demo tenant **Demo Bank** (`demo-bank`): staff users, the agent "Aarya", the mock
bank's OpenAPI tools and MCP server, and the four sample PDFs (indexed into Elasticsearch).
Staff logins: `admin@ / supervisor@ / agent@ / auditor@demo-bank.example`, password `DemoBank!2026secure`.
The mock bank's OTP is always `123456`.

Phone: inbound calls arrive on a **LiveKit Phone Number** (or a carrier SIP trunk into LiveKit SIP); bind numbers to
the agent with `python -m scripts.provision_telephony --tenant demo-bank --number <E.164>` — see
[docs/voice.md](docs/voice.md#phone-numbers-setup). Every call gets its own session; caller-id only identifies.

Voice: `docker compose --profile voice up -d` adds a local LiveKit server and the voice worker. Set
`STT_PROVIDER`/`TTS_PROVIDER` and their keys (e.g. `DEEPGRAM_API_KEY`, `SARVAM_API_KEY`) — see [docs/voice.md](docs/voice.md).
Without keys you can still exercise the full voice path from text with `POST /voice/simulate` (dev only).

### Without Docker

```bash
uv venv --python 3.12 && uv pip install -e ".[dev,voice]"
python -m scripts.generate_sample_docs           # (PDFs are already committed)
# in-process stores, SQLite, offline LLM:
DATABASE_URL=sqlite+aiosqlite:///./dev.db DB_AUTO_CREATE=true AUTO_SEED=true REDIS_URL= KNOWLEDGE_BACKEND=memory \
  uvicorn mock_bank.app:app --port 8100 &  uvicorn app.main:app --port 8000
```

## The prototype flows

| Request | Path |
|---|---|
| Chat: *"What are home loan foreclosure charges?"* | intent `KNOWLEDGE_QUERY` → `search_knowledge` → Elasticsearch hybrid (BM25 + kNN, RRF, rerank, tenant/validity filters) → answer with `[1]` citation |
| Chat: *"What is my loan balance?"* | `CUSTOMER_DATA_QUERY` → `get_loan_details` (mock bank **OpenAPI**) → policy ALLOW (fully authenticated) |
| Voice: *"Mera credit card ka outstanding kitna hai?"* | LiveKit → STT → same runtime → `get_card_status` (mock bank **MCP**) → Hinglish reply → speech rendering ("23 hazaar 450 rupaye") → TTS |
| *"Block my card."* | clarifies which card → `block_card` → policy `REQUIRE_CONFIRMATION` → "yes" → executes the frozen call |
| *"Transfer ₹100,000 to Rahul."* | workflow: `find_beneficiary` → validate (exactly one active beneficiary) → `transfer_money` (CRITICAL) → `REQUIRE_AUTH` (transaction OTP bound to this exact action) → `REQUIRE_CONFIRMATION` of the resolved beneficiary → execute with an idempotency key → verify by status lookup → receipt |
| *"What's my balance and my last transactions?"* | two independent reads in one parallel wave, each policy-checked |
| *"Yes, but make it ₹50,000"* (at the confirmation) | the ₹1,00,000 action is **not** executed; a new action is created and needs its own OTP + confirmation |

Also: ₹5 lakh+ transfers go to maker-checker approval (`/approvals`), fraud reports create an urgent
handoff with full context, and customers can move between chat and voice mid-conversation.

## Choosing the LLM

`LLM_PROVIDER` selects an implementation of `app/llm/base.py:LLMProvider`:

* `openai` — any OpenAI-compatible Chat Completions endpoint (OpenAI, Azure gateways, LiteLLM, ...)
* `local` — self-hosted vLLM / TGI / Ollama / llama.cpp behind an OpenAI-compatible API (on-prem deployments)
* `rule_based` — deterministic offline stand-in (default). It uses the same runtime, gateway and policy path
  with no privileges, so demos, CI and the evaluation suite run without an API key. It is not a substitute
  for a real model in production (limited language coverage and reasoning).

`LLM_FALLBACK_PROVIDER` adds failover before the first streamed token. Embeddings and reranking are
configured independently (`EMBEDDING_PROVIDER`, `RERANKER_PROVIDER`); the default hashing embeddings are
for offline use only — use a multilingual embedding model in production.

## Tests, evaluation, load

```bash
pytest                                                    # 186 unit / integration / security / evaluation tests
BFSI_INFRA_TESTS=1 pytest tests/integration/test_real_infrastructure.py   # real Postgres/Redis/ES, incl. 3 workers on Redis
docker compose exec backend python -m scripts.run_evaluation              # scenario suite on chat AND voice
python -m scripts.load_test --users 25 --turns 8                          # concurrency + latency percentiles
```

The evaluation suite (`app/evaluation/scenarios.py`) runs 21 scenarios — balance, transactions, loans, cards,
payments, FAQs, rates, card block, transfer, cancel, maker-checker, fraud, handoff, plus security cases
(prompt injection, unauthenticated access, claimed authentication, voice-biometric-only, cross-customer,
tenant escape, PII storage, post-OTP argument swap) — on both channels and reports intent / tool /
authorization / grounding / safety accuracy and latency. It is also exposed at `POST /evaluation/runs`.

The orchestration suites (`tests/unit/test_execution_engine.py`, `tests/integration/test_orchestration.py`) cover DAG
scheduling, plan policy, parallel reads, financial non-concurrency, confirmation amendments, read-timeout retry,
transfer timeout → verification (no resend), lost-before-commit, unverifiable outcome → human, bank-side idempotency,
worker crash → reconciliation on another worker, 100 concurrent sessions, 10 concurrent requests on one session and
a three-worker simulation. `tests/integration/test_telephony.py` covers phone calls: per-call sessions for three
simultaneous callers on one number, caller-id never authenticating, no raw numbers persisted, hangup never cancelling
a submitted transfer, "wait!" after submission, barge-in outcome reporting and capacity limits. The mock bank supports fault injection for these (`POST /_admin/faults`).

## Horizontal scaling

Run as many backend workers as needed behind a load balancer; every turn state lives in Redis (session state, renewed
per-session locks, versioned saves) and Postgres (durable workflows, audit). Production refuses to start without Redis.
In-process concurrency limits are per worker; enable `DISTRIBUTED_TOOL_LIMITS` for cluster-wide limits per
institution API. Details and settings: [docs/orchestration.md](docs/orchestration.md).

## API

| | |
|---|---|
| Auth & tenancy | `POST /auth/login`, `POST /tenants`, `GET /tenants/me`, `POST/GET /users`, `POST/GET /agents`, `GET/PATCH /agents/{id}` |
| Sessions | `POST /sessions` (optional bank-IdP `customer_assertion`), `GET /sessions/{id}`, `POST /sessions/{id}/auth/{assertion,identify,otp/send,otp/verify}`, `POST /sessions/{id}/close`, `GET /sessions/{id}/messages` |
| Chat | `POST /chat/message`, `WS /ws/chat/{session_id}?token=` (streams `message.delta`, `tool.started`, `tool.completed`, `workflow.progress`, `verification.completed`, `auth.required`, `confirmation.required`, `handoff.initiated`, `message.completed`, ...) |
| Voice | `POST /voice/session` (new, or switch an existing session to voice), `POST /voice/token`, `POST /voice/simulate` (dev) |
| Knowledge | `POST /documents` (versioned upload), `GET /documents`, `POST /documents/{id}/reindex`, `POST /knowledge/search` |
| Integrations & tools | `POST/GET /integrations`, `POST /integrations/{id}/test`, `POST /integrations/{id}/import-openapi`, `POST/GET /mcp/servers`, `GET /mcp/servers/{id}/tools`, `POST /mcp/servers/{id}/discover`, `GET /tools`, `PATCH /tools/{id}`, `POST /tools/{id}/test` |
| Governance | `POST/GET /policies`, `GET /approvals`, `POST /approvals/{id}/decision`, `GET /audit/events`, `GET /audit/verify` |
| Handoff | `POST /handoff`, `GET /handoff/queue`, `POST /handoff/{id}/{accept,reply,resolve}`, `WS /ws/desk?token=` |
| Ops | `GET /health`, `GET /ready`, `GET /metrics`, `GET /monitoring/summary`, `GET /monitoring/activity`, `POST /evaluation/runs`, `GET /evaluation/runs/{id}` |

Interactive docs: `http://localhost:8000/docs`.

Failed tool calls carry a `failure_kind` (on `tool.failed` events as `outcome`): `not_executed` (never sent),
`rejected` (the institution declined it — nothing processed) or `unknown` (sent but unconfirmed). Clients must only
tell a customer "no amount was debited" for the first two.

## Repository layout

```
app/
  agents/        runtime (channel-agnostic facade), orchestrator (turn loop), planner (intent), tool_selector,
                 state (shared session model), prompts (system prompt + deterministic security prompts),
                 execution/ (planner DAG, executor, verifier, concurrency limits + capacity pools, durable workflows, templates)
  channels/      base (InteractionChannel, TurnRunner), chat/ (REST gateway, WebSocket), voice/ (LiveKit worker,
                 call lifecycle + tokens, STT/TTS selection, VAD/turn detection, LiveKit SIP / phone numbers)
  llm/           LLMProvider contract, OpenAI-compatible, local, resilient wrapper, rule-based stand-in, factory
  knowledge/     ingestion (parser, chunker, metadata, pipeline), embeddings, retrieval (Elasticsearch, in-memory,
                 hybrid RRF, rerankers), rag, citations, documents (versioning)
  tools/         schemas (the one Tool contract + trusted execution metadata), registry, router (Tool Gateway),
                 failures (failure taxonomy + retry rules), permissions, rest/, mcp/, adapters/
  policies/      engine, rules (tenant DSL + defaults), risk (contextual scoring), approval (action binding, maker-checker)
  auth/          staff + customer authentication, RBAC, JWT, OAuth2 client credentials
  sessions/      session manager (Redis/in-memory store, per-session locks), conversation memory
  security/      PII detection, redaction profiles, prompt-injection heuristics, secrets, hash-chained audit
  integrations/  integration lifecycle, encrypted credentials, health
  escalation/    handoff (shared chat/voice), human agent desk
  i18n/          language detection (11 languages + code-mixing), lexicon, Indian number/speech formatting
  database/      models, tenant-scoped repository, engine
  observability/ OpenTelemetry tracing, Prometheus metrics, PII-safe JSON logging
  evaluation/    scenarios, runner, metrics
mock_bank/       sandbox institution: REST + OpenAPI (accounts, loans, payments, OTP) and an MCP server (cards, transfers)
scripts/         seed, demo, run_evaluation, load_test, generate_sample_docs, provision_telephony
migrations/      Alembic (initial schema; orchestration: tool execution metadata, agent_workflows; voice_calls)
docs/            architecture, orchestration, security, integrations, voice
```

## Status and known limitations

This is a working prototype, verified end-to-end against PostgreSQL, Redis, Elasticsearch, the mock bank
(REST + MCP) and a local LiveKit server (worker registration and agent dispatch). Before production:

* **Live audio is unverified here** — the STT/TTS vendors (Deepgram/Sarvam/ElevenLabs) need API keys. The LiveKit
  pipeline itself is tested in text mode (`tests/integration/test_livekit_adapter.py`).
* The default `rule_based` LLM and hashing embeddings are offline stand-ins; evaluate with your chosen model and a
  multilingual embedding model, and re-run the evaluation suite (it is the regression gate).
* Customer assertions use a shared HS256 secret; production should verify RS256/ES256 against each institution's JWKS.
  Secret references `vault:`/`aws-sm:` are reserved hooks that fail closed until wired to a secret manager.
* Registry / policy / agent caches are per-process with a 30 s TTL; with multiple replicas, admin changes take up
  to 30 s to apply everywhere (add Redis pub/sub invalidation if that matters).
* Voice metrics are recorded in the LiveKit worker's job processes but not yet exported (needs Prometheus
  multiprocess mode or an OTLP metrics exporter); chat/common metrics are served at `/metrics`.
* No rate limiting on the public `POST /sessions` / chat endpoints — put an API gateway / WAF in front.
* Orchestration: workflow templates are code-defined (transfer, card block); other mutating tools are verified only by
  their response and escalate when it is ambiguous. In-process concurrency limits are per worker (cluster-wide limits
  cover concurrency groups only, opt-in). See [docs/orchestration.md](docs/orchestration.md#14-known-limitations).
* Elasticsearch runs without security in the dev compose file; enable TLS + auth for any shared environment.
