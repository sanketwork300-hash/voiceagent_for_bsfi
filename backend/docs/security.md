# Security model

**Principle: the LLM is untrusted.** It may be wrong, manipulated by the customer, or manipulated by text in
documents/tool outputs. Every control below holds no matter what the model emits.

| Threat | Control |
|---|---|
| Model calls a dangerous tool | Tools are offered per intent (no transfer tool on knowledge questions); the gateway rejects any call to a tool not offered this turn, internal tools (`send_otp`, `verify_otp`, `lookup_customer`) are never offered, unknown tools are rejected — all audited (`security.tool_not_offered`) |
| Model targets another customer | `customer_id` is an injected parameter: removed from the model's schema, overwritten from the session, override attempts audited |
| Model bypasses authentication | Auth state lives in the server-side session; it changes only through bank IdP assertions, the institution's OTP verification, or identity lookups — never through conversation text ("I'm already verified" does nothing) |
| Weak factors for risky actions | HIGH/CRITICAL risk requires a strong factor (bank assertion or OTP). Caller-id and voice biometrics cap at `PARTIALLY_AUTHENTICATED`; voice biometrics alone never authorise a transfer or card block |
| Argument swap after confirmation | Confirmation and transaction OTP are bound to the SHA-256 of `{tool, args}`; resumes execute the frozen call; transaction authentication is single-use |
| Policy bypass | `PolicyEngine` is the only path to execution: tenant DENY rules → auth level/factor → risk + maker-checker → explicit confirmation. Malformed tenant rules are rejected at creation |
| Prompt injection (user or documents) | Instruction-like spans in retrieved text are defanged; system prompt marks tool/document text as data; suspected user injections are audited. The structural controls above are the real defence |
| PII leakage | Secrets (OTP, PIN, CVV, passwords) are stripped before storage and before the LLM; tool outputs are masked (card/account/Aadhaar/PAN) before the model sees them; logs/traces use the strictest profile; audit uses partial masking |
| Tenant escape | Tenant id from the token, never the request body; tenant filter inside every knowledge store query; tenant-scoped repository; sessions from another tenant are reported as "not found" |
| Tampering with history | Audit events are hash-chained per tenant (advisory-locked across replicas, unique sequence); `GET /audit/verify` detects edits/deletions |
| Credential exposure | Integration credentials are Fernet-encrypted at rest (or `env:` references); never returned by the API |
| Brute force OTP | Failures counted per session; lockout after `MAX_AUTH_FAILURES` triggers an `AUTHENTICATION_FAILURE` handoff |

Maker-checker: tenant admins cannot approve money movement (`approval:decide` is held by supervisors).

## What is tested

`tests/security/` drives an adversarial scripted LLM (un-offered/internal/invented tools, smuggled
`customer_id`, invalid arguments, post-confirmation argument swap), plus document injection, cross-tenant
access, forged / `alg=none` / wrong-type tokens, invalid customer assertions (expired, wrong audience,
wrong key), secret persistence across messages / tool executions / audit, log redaction, and audit tamper
detection. The evaluation suite repeats the security scenarios end-to-end on chat and voice.

## Production checklist

* Replace HS256 customer assertions with RS256/ES256 + JWKS per institution; rotate `JWT_SECRET`, set `ENCRYPTION_KEY`.
* Wire `vault:` / `aws-sm:` secret references to the secret manager in use.
* Enable Elasticsearch / Redis / Postgres auth + TLS; mTLS to institution APIs where required.
* Rate-limit `POST /sessions` and chat endpoints at the gateway; add bot protection to anonymous web chat.
* Data residency: keep inference on-prem (`LLM_PROVIDER=local`) or with an approved in-region provider.
