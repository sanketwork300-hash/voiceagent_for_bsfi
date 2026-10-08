# BFSI AI agent platform

Chat and voice AI agents for banks, NBFCs, insurers and fintechs — one agent runtime behind both channels, with the
institution's APIs as the source of truth and a policy engine as the authority on what the AI may do.

| | |
|---|---|
| [`backend/`](backend/README.md) | FastAPI agent runtime: LLM orchestration, RAG (Elasticsearch), REST/OpenAPI/MCP tool gateway, policy engine, customer authentication, LiveKit voice worker, handoff, audit, observability, evaluation |
| [`frontend/`](frontend/README.md) | Next.js customer assistant (chat + voice) and operator/admin console |

```bash
cd backend && cp .env.example .env && docker compose --profile ui up -d --build --wait
# customer assistant: http://localhost:3000/assist   ·   console: http://localhost:3000 (admin@demo-bank.example / DemoBank!2026secure)
```

Add `--profile voice` for a local LiveKit server and the voice worker (needs STT/TTS vendor keys — see `backend/docs/voice.md`).
