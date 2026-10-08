# Ledgerline — BFSI AI agent frontend

Next.js 16 frontend for the BFSI chat + voice agent platform (`../backend`). It is a client of the backend's
agent runtime: it renders, streams and configures, but never decides — authentication state, policy decisions,
tool results and money movement always come from the backend.

Two audiences, two experiences:

| | Route | Sees |
|---|---|---|
| **Customers** | `/assist` (chat), `/assist/voice` (call) | Plain answers, sources, verification and confirmation steps, receipts, a person when needed. No tool names, scores, policies or prompts. |
| **Bank staff** | everything else (sign-in + MFA) | Tool traces, sources with scores, policy decisions, latency, audit, handoff context — and configuration for administrators. Never secrets, OTPs, PINs or tokens. |

## Run it

```bash
# backend stack (from ../backend)
docker compose up -d --build --wait

# frontend
cp .env.example .env.local
npm install
npm run dev            # http://localhost:3000
```

Or everything in containers: `cd ../backend && docker compose --profile ui up -d --build --wait`.

Demo sign-in: organization `demo-bank`, `admin@demo-bank.example` (also `supervisor@`, `agent@`, `auditor@`), password
`DemoBank!2026secure`, then any 6-digit MFA code except `000000`. Customer OTPs are always `123456`.

## How it talks to the backend

```
Browser ──REST──▶ /api/backend/* (same-origin BFF) ──▶ FastAPI      staff token lives in an httpOnly, SameSite=Strict cookie
        ──WS────▶ ws://backend/ws/chat/{session}?token=…            streaming events (message.delta, tool.*, auth.required, …)
        ──WebRTC▶ LiveKit room (voice)                              participant token from POST /voice/session
```

* **BFF proxy** (`src/app/api/backend/[...path]/route.ts`): attaches the staff token server-side, rejects cross-origin
  mutations (CSRF), adds a support reference (`REQ-…`) to every request, never forwards cookies or `Set-Cookie`.
  Customer calls carry their own short-lived session token instead, so staff and customer identities never mix.
* **Typed API client** (`src/lib/api/client.ts`): the only place requests are made. Mocks are imported through the same
  interface and labelled in the UI as sample data.
* **Conversation reducer** (`src/lib/chat/conversation.ts`): a pure function from backend events to UI state — used
  by chat, voice transcript history and the REST fallback alike. Financial actions become *slips* (verify, confirm,
  approval, receipt, failure), visually distinct from AI text.
* **Voice state machine** (`src/lib/voice/machine.ts`): `idle → connecting → connected → listening ⇄ processing →
  speaking`, with `interrupted`, `handoff`, `ended`, and verification/mute as flags. Two transports feed it:
  * `livekit` — LiveKit room; agent state from `lk.agent.state`, transcripts from `lk.transcription`, keypad digits
    sent as `lk.chat` text input. Needs the voice worker and STT/TTS keys.
  * `demo` (default) — browser speech recognition and synthesis routed through `POST /voice/simulate`, i.e. the
    backend's real voice channel minus audio transport. Supports barge-in (speaking over the reply, or *Interrupt*).
    Without microphone access you can type.

Chat and voice share one backend session: switching keeps the conversation, authentication and pending action.

## What's mocked (and marked as such)

| Feature | Why | Replace with |
|---|---|---|
| Customer 360 (`/customers`) | Backend stores no customer records by design; no customer API yet | an institution CRM/core integration |
| Staff MFA | No backend MFA endpoint | `POST /auth/mfa/verify` (TOTP/WebAuthn) |
| Device sessions, workflows, feedback | No endpoints | `/auth/sessions`, `/workflows`, `/conversations/{id}/feedback` |
| Demo customer sign-in | Stands in for the bank's app/netbanking IdP | the institution's IdP issuing customer assertions |

Everything else (chat, voice, sessions, conversations, documents, knowledge search, integrations, OpenAPI import,
MCP, tools + live test console, policies, approvals, handoff desk, audit + chain verification, monitoring,
evaluation, agents, users) runs against real backend endpoints.

## Tests

```bash
npm run typecheck && npm run lint
npm test                                              # 48 unit + component tests (Vitest)
npm run build && npm start -- -p 3100 &               # e2e runs most reliably against a production build
E2E_BASE_URL=http://localhost:3100 npm run test:e2e   # 24 Playwright tests incl. axe accessibility checks
```

E2E covers sign-in/MFA/sign-out, balance, RAG with sources, transfer with transaction OTP → confirmation →
receipt, cancellation, OTP lockout → handoff, explicit handoff, Hinglish, voice call → chat continuity, keypad
OTP masking, maker-checker across two browsers, the live handoff desk, the tool test console, and security:
permission gating (UI and API), httpOnly/SameSite staff cookie, BFF CSRF and auth, session-scoped customer tokens,
WebSocket token rejection, LiveKit token authorization and XSS. The global setup resets the sandbox mock bank.

## Layout

```
src/app/            routes: /assist (customer), /(console)/* (staff), /auth/*, /api/* (BFF, auth, demo IdP)
src/components/     ui (design system) · layout · chat · voice · handoff · customer · knowledge · integrations ·
                    tools · mcp · policies · approvals · audit · monitoring · agent-builder · authentication
src/lib/            api (client, http, errors, mocks) · websocket · livekit · voice · chat · policies · permissions · auth
src/hooks/          use-conversation, use-voice, use-profile
src/store/          Zustand: UI state, active customer session (tab-scoped)
src/types/          domain + event contracts mirroring the backend
```

Design tokens are in `src/app/globals.css` (`--background`, `--surface`, `--border`, `--muted`, `--success`, …).
Black/grey/white with semantic accents only; Geist Sans for reading, Geist Mono for amounts, IDs and latencies.
The brief's `#777` grey is lifted to `#808080` where used for text, to meet WCAG AA contrast.

## Known limitations

* CSP allows inline scripts (needed by Next's runtime without nonces); move to nonce-based CSP via `proxy.ts` for production.
* Customer session tokens are kept in `sessionStorage` (tab-scoped, short-lived) because the WebSocket needs them.
* Live LiveKit audio hasn't been exercised here (no STT/TTS vendor keys); the transport is wired to LiveKit's documented
  agent attributes and text streams.
* Some backend caches are per-process (≈30 s), so admin changes can take a moment to show on other replicas.
