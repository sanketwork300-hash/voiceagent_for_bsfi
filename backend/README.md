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
                  planner (intent) · LLM loop · pending-action state machine
                           │
        ┌──────────────────┼──────────────────────┐
   search_knowledge     Tool Gateway  ───────►  Policy Engine (auth level + factor strength,
   RAG (Elasticsearch   (REST · OpenAPI ·         risk, maker-checker, confirmation)
   BM25+kNN+rerank)      MCP · adapters)               │
                           └──────────► Bank / NBFC APIs & MCP servers (system of record)
```

The platform never touches an institution's database: customer data and operations go through REST,
OpenAPI-described APIs, MCP servers or registered adapters. The LLM proposes tool calls; only the policy
engine can authorise them.

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
| *"Transfer ₹100,000 to Rahul."* | `transfer_money` (CRITICAL) → `REQUIRE_AUTH` (transaction OTP bound to this exact action) → `REQUIRE_CONFIRMATION` → execute → transaction auth consumed |

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
pytest                                                    # 115 unit / integration / security / evaluation tests
BFSI_INFRA_TESTS=1 pytest tests/integration/test_real_infrastructure.py   # against real Postgres/Redis/ES
docker compose exec backend python -m scripts.run_evaluation              # scenario suite on chat AND voice
python -m scripts.load_test --users 25 --turns 8                          # concurrency + latency percentiles
```

The evaluation suite (`app/evaluation/scenarios.py`) runs 21 scenarios — balance, transactions, loans, cards,
payments, FAQs, rates, card block, transfer, cancel, maker-checker, fraud, handoff, plus security cases
(prompt injection, unauthenticated access, claimed authentication, voice-biometric-only, cross-customer,
tenant escape, PII storage, post-OTP argument swap) — on both channels and reports intent / tool /
authorization / grounding / safety accuracy and latency. It is also exposed at `POST /evaluation/runs`.

## API

| | |
|---|---|
| Auth & tenancy | `POST /auth/login`, `POST /tenants`, `GET /tenants/me`, `POST/GET /users`, `POST/GET /agents`, `GET /agents/{id}` |
| Sessions | `POST /sessions` (optional bank-IdP `customer_assertion`), `GET /sessions/{id}`, `POST /sessions/{id}/auth/{assertion,identify,otp/send,otp/verify}`, `POST /sessions/{id}/close`, `GET /sessions/{id}/messages` |
| Chat | `POST /chat/message`, `WS /ws/chat/{session_id}?token=` (streams `message.delta`, `tool.started`, `tool.completed`, `auth.required`, `confirmation.required`, `handoff.initiated`, `message.completed`, ...) |
| Voice | `POST /voice/session` (new, or switch an existing session to voice), `POST /voice/token`, `POST /voice/simulate` (dev) |
| Knowledge | `POST /documents` (versioned upload), `GET /documents`, `POST /documents/{id}/reindex`, `POST /knowledge/search` |
| Integrations & tools | `POST/GET /integrations`, `POST /integrations/{id}/test`, `POST /integrations/{id}/import-openapi`, `POST /mcp/servers`, `GET /mcp/servers/{id}/tools`, `POST /mcp/servers/{id}/discover`, `GET /tools`, `PATCH /tools/{id}`, `POST /tools/{id}/test` |
| Governance | `POST/GET /policies`, `GET /approvals`, `POST /approvals/{id}/decision`, `GET /audit/events`, `GET /audit/verify` |
| Handoff | `POST /handoff`, `GET /handoff/queue`, `POST /handoff/{id}/{accept,reply,resolve}`, `WS /ws/desk?token=` |
| Ops | `GET /health`, `GET /ready`, `GET /metrics`, `POST /evaluation/runs`, `GET /evaluation/runs/{id}` |

Interactive docs: `http://localhost:8000/docs`.

## Repository layout

```
app/
  agents/        runtime (channel-agnostic facade), orchestrator (turn loop), planner (intent), tool_selector,
                 state (shared session model), prompts (system prompt + deterministic security prompts)
  channels/      base (InteractionChannel, TurnRunner), chat/ (REST gateway, WebSocket), voice/ (LiveKit worker,
                 voice sessions + tokens, STT/TTS selection, VAD/turn detection, SIP telephony)
  llm/           LLMProvider contract, OpenAI-compatible, local, resilient wrapper, rule-based stand-in, factory
  knowledge/     ingestion (parser, chunker, metadata, pipeline), embeddings, retrieval (Elasticsearch, in-memory,
                 hybrid RRF, rerankers), rag, citations, documents (versioning)
  tools/         schemas (the one Tool contract), registry, router (Tool Gateway), permissions, rest/, mcp/, adapters/
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
scripts/         seed, demo, run_evaluation, load_test, generate_sample_docs
migrations/      Alembic (initial schema)
docs/            architecture, security, integrations, voice
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
* Elasticsearch runs without security in the dev compose file; enable TLS + auth for any shared environment.
