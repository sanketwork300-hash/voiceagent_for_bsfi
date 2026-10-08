# Agent orchestration & execution engine

How one agent turn turns model-proposed tool calls into safe, concurrent, verified work — and how that scales across
many callers and many backend workers. Code: `app/agents/orchestrator.py` (conversation) and `app/agents/execution/`
(execution).

> The model **proposes**. The policy engine **authorises**. The execution engine **schedules and verifies**.
> The LLM is never the workflow engine and never the authority over money movement.

## 1. Architecture

```
 caller ──► channel (chat REST/WS · voice LiveKit) ──► AgentRuntime ──► Orchestrator (per turn, under the session lock)
                                                                          │
   REASON   planner.reason()           intent, entities, constraints, required info, risk  (structured; no chain of thought)
   PLAN     ExecutionPlanner.extend()  LLM tool calls ─► dedupe ─► schema check ─► templates ─► DAG edges ─► plan policy
   POLICY   ToolGateway (per step)     offered? ─► strip/inject params ─► JSON schema ─► PolicyEngine (auth, risk, approval,
                                       confirmation bound to the action hash)
   ACT      PlanExecutor.stream()      waves: parallel reads │ one mutation alone │ write-ahead │ shielded submit
                                       ToolConcurrencyManager: global · group · tool · session (+ Redis leases, opt-in)
   VERIFY   Verifier                   read-only lookup of the mutation's real outcome (idempotency key / state check)
   RESPOND  LLM, from structured per-call tool results — or deterministic text for holds / unconfirmed outcomes
                                                                          │
                       WorkflowStore (Postgres `agent_workflows`) ◄───────┘  SessionState (Redis, versioned) + PG mirror
```

| Module | Responsibility |
|---|---|
| `execution/models.py` | `ReasoningResult`, `ExecutionStep`, `WorkflowState`, `StepResult`, `VerificationResult`, `ExecutionResult`, statuses |
| `execution/planner.py` | DAG construction + plan-level policy (mutation budget, step budget, cycles, mutation-ordering invariant) |
| `execution/templates.py` | trusted server-side workflows (transfer, card block) and their deterministic checks |
| `execution/executor.py` | scheduling, waves, concurrency slots, write-ahead, shielded mutations, verification folding |
| `execution/verifier.py` | SUCCESS / FAILED / PARTIAL / UNKNOWN / TIMEOUT; bounded polling; never retries the action |
| `execution/concurrency.py` | in-process semaphores and the Redis lease limiter |
| `execution/workflow.py` | durable `WorkflowState` (optimistic versioning) and the in-flight submission registry |
| `tools/failures.py` | failure categories, dispositions and the retry rule |
| `tools/schemas.py` | trusted `ExecutionMetadata` and `resolve_execution` |

## 2. The agent loop

```
lock session ─► reconcile earlier unverified mutation (verify, never resend) ─► OTP / pending-action handling
  ─► REASON ─► offered tools (intent-scoped)
  ─► repeat ≤ MAX_AGENT_ITERATIONS:
        LLM ─► no tool calls? ─► RESPOND (done)
            └► tool calls ─► PLAN ─► POLICY+ACT+VERIFY (one execution pass)
                 ├─ a step HELD (auth / confirmation / approval) ─► deterministic prompt, workflow persisted ─► stop
                 ├─ a mutation UNKNOWN after verification ─► "couldn't confirm, not resent" + human handoff ─► stop
                 └─ otherwise ─► one structured tool message per LLM tool_call id ─► next iteration
```

Limits (all configuration, validated at startup): `MAX_AGENT_ITERATIONS`, `MAX_WORKFLOW_STEPS`, `WORKFLOW_TIMEOUT`
(per execution pass), per-tool timeouts (`timeout_seconds`, default `DEFAULT_TOOL_TIMEOUT`). A workflow that hits its
deadline cancels steps that have not started; a mutation already on the wire is never abandoned (see §6).

**Case A — independent calls** (`get_account_balance`, `get_recent_transactions`): no edges, one wave, `asyncio.gather`
under the concurrency slots. **Case B — dependent calls**: edges come only from trusted sources — template wiring and the
tool's registered `depends_on` — never from the order the model happened to emit.

Every LLM tool call gets its own tool message with structured JSON (`ok`, `status`, `data`/`error`, `outcome`,
`policy_decision`, `verification`). Results are never flattened into one string. Duplicate calls in one turn are executed
once and answered from the same step.

## 3. Trusted execution metadata

`ToolDefinition.execution` (`ExecutionMetadata`): `operation_type` READ/WRITE/VERIFY, `side_effect`
NONE/ACCOUNT_READ/ACCOUNT_MUTATION/FINANCIAL_MUTATION/EXTERNAL_SIDE_EFFECT, `parallel_safe`, `idempotent`,
`requires_human_approval`, `depends_on`, `concurrency_group`, `max_concurrency` — next to the existing `risk_level`,
`min_auth_state`, `requires_confirmation`, `timeout_seconds`.

It is registered server-side — OpenAPI `x-bfsi-operation-type / -side-effect / -parallel-safe / -idempotent /
-concurrency-group / -depends-on / -max-concurrency`, MCP `_meta.bfsi.execution`, or `PATCH /tools/{id}` — and merged
conservatively by `resolve_execution`:

* unknown ⇒ WRITE, ACCOUNT_MUTATION, not parallel-safe;
* a write transport (POST / non-`readOnlyHint` MCP) can never be declared READ;
* a mutating side effect is always WRITE; **mutations are never parallel-safe**, whatever is declared.

Nothing in LLM output can set or change it. Workflow-bound parameters (e.g. `beneficiary_id`) are removed from the
model's schema and stripped + audited (`security.injected_param_override_attempt`) if the model supplies them anyway.

## 4. Execution DAG

```
A → B → C          (declared depends_on)       waves: [A] [B] [C]
A, B → C           (C depends on A and B)      waves: [A, B] [C]          — A and B in parallel, outputs available to C
```

Edge kinds: **data edges** (`depends_on` — must COMPLETE; a failed dependency SKIPS the dependant) and **ordering edges**
(`after` — must be finished, any outcome). Rules applied by the planner:

1. every mutation runs **after all earlier steps of its batch** and after the previous mutation;
2. any step proposed after a mutation runs after it (it observes the effect);
3. a verification step depends on its mutation and runs when that mutation is COMPLETED or UNKNOWN;
4. the plan is checked for cycles and for the invariant *no two mutations are concurrently schedulable*; a violating
   batch is rejected as a whole (fail closed).

Plan-level policy: at most `MAX_MUTATIONS_PER_PLAN` state-changing actions per request (default **1**; extras become
REJECTED steps with an explanation for the model), at most `MAX_WORKFLOW_STEPS` steps.

### Templates (deterministic server-side workflows)

```
transfer_money:  s2 find_beneficiary(name) ─► s3 CHECK single active beneficiary ─► s1 transfer_money(+beneficiary_id)
                                                                                     ─► s4 VERIFY get_transfer_status(idempotency_key)
block_card:      s1 block_card ─► s2 VERIFY get_card_status (card is BLOCKED)
```

A template applies only when the tenant has the helper tools; otherwise the action runs as one policy-gated step, as
before. The customer confirms the **resolved** beneficiary ("…to Rahul (Rahul Verma, account XXXX4521)"), and that
beneficiary id is part of the action hash the confirmation / transaction OTP / approval is bound to.

## 5. Parallel vs sequential vs financial

| | Scheduled | Concurrency | Retries | Verification |
|---|---|---|---|---|
| READ / VERIFY (parallel-safe) | together in a wave (≤ `MAX_PARALLEL_TOOLS`) | global · group · tool · session slots | yes: exponential backoff + jitter, `READ_RETRY_COUNT`, Retry-After honoured, bounded by the workflow deadline | — |
| WRITE (account mutation) | alone; never overlaps any other step | same slots | only if idempotent **and** carrying an idempotency key (`WRITE_RETRY_COUNT`) | template verify step, else the explicit response |
| FINANCIAL_MUTATION | alone; never while another mutation is in flight or unverified | same slots | **never** | status lookup by idempotency key |

A later mutation is SKIPPED while an earlier one is UNKNOWN: the engine never proceeds past an ambiguous money movement.

## 6. Financial safety, idempotency and verification

* **Action binding.** `action_hash = sha256(tool, exact args incl. amount/payee/beneficiary/account/currency, tenant,
  session, customer)`. Confirmations, transaction OTPs and maker-checker approvals are valid for exactly one hash.
* **Strict confirmation.** Only a short unconditional "yes/haan/ok" confirms. *"Yes, but make it ₹50,000"* is an
  amendment: the frozen ₹1,000 action is dropped (audited `action.amended`), the message is processed as a new request,
  and the new action needs its own transaction OTP and confirmation. A long or hedged "yes" re-asks.
* **Idempotency keys** are deterministic per (tenant, workflow, step, action hash) and sent to the institution as the
  REST `Idempotency-Key` header and MCP `params._meta.idempotency_key`. A re-send of the same step carries the same key;
  the mock bank returns the original result for a repeated key (de-duplication at the system of record, not only in the app).
* **Write-ahead.** After policy ALLOW and before sending, the step is persisted as SUBMITTING with its key. If that write
  fails, nothing is sent.
* **Shielded submission.** The submission runs in its own task; a voice barge-in cancels the turn but not the transfer.
  The turn waits (bounded) for in-flight writes before releasing the session lock, and the result is recorded either way.
* **Ambiguous result** (timeout, 5xx, lost connection after send) ⇒ step UNKNOWN ⇒ **no retry** ⇒ verification:

```
explicit success response + lookup SUCCESS / unavailable   → COMPLETED (receipt)
ambiguous + lookup SUCCESS                                  → COMPLETED — recovered; receipt from the bank's record
ambiguous + no record after the in-flight window            → FAILED — "was not processed, nothing has changed"
ambiguous + lookup down / timeout / bank says PENDING       → UNKNOWN — "couldn't confirm, not sent again" + human handoff
success response but lookup disagrees (amount/beneficiary)  → PARTIAL → UNKNOWN → human
```

  Success is reported to the customer (and the receipt event emitted) only after verification.
* **Recovery.** A worker that dies after sending leaves the step SUBMITTING. The next turn — on any worker — reconciles:
  verifies by idempotency key, tells the customer the outcome, and invalidates the stale pending confirmation so a
  repeated "yes" can never resend it.

### Failure taxonomy (`app/tools/failures.py`)

`VALIDATION_ERROR, AUTHENTICATION_ERROR, AUTHORIZATION_ERROR, RATE_LIMIT, TIMEOUT, NETWORK_ERROR, BUSINESS_RULE_FAILURE,
NOT_FOUND, CONFLICT, DUPLICATE, PARTIAL_FAILURE, UNKNOWN, DEPENDENCY_UNAVAILABLE` (+ `POLICY_BLOCKED` for holds/denials),
each with a disposition `retryable | non_retryable | requires_verification | requires_human` derived from the category
and the tool's trusted metadata. Stored on `tool_executions.failure_category` with `attempts`, `workflow_id`, `step_id`,
`idempotency_key`. The existing `failure_kind` (`not_executed | rejected | unknown`) is unchanged for clients.

## 7. Workflow state

`WorkflowState`: `workflow_id, workflow_type, status, steps (current / completed / failed / pending), context
(intent, language, structured reasoning, offered tools), idempotency_key, created_at, updated_at, deadline, version,
worker_id`. Statuses: `CREATED, PLANNING, WAITING_FOR_AUTH, WAITING_FOR_CONFIRMATION, WAITING_FOR_APPROVAL, EXECUTING,
VERIFYING, COMPLETED, FAILED, ESCALATED, CANCELLED, EXPIRED`.

Workflows with a mutation or a hold are persisted in `agent_workflows` (write-ahead, after each wave, on holds and
cancellation) with optimistic versioning, so two workers can never both advance one workflow. Read-only workflows stay
in memory — there is nothing to recover and it keeps voice latency unchanged. `PendingAction` now carries
`workflow_id` / `step_id`; resuming continues the workflow with exactly the tools that were offered when it was planned.
Pending actions created before the upgrade still resume through the single-step legacy path.

## 8. Concurrency limits — what is and is not global

| Limit | Setting | Scope |
|---|---|---|
| global | `TOOL_GLOBAL_CONCURRENCY` | **one worker process** (asyncio semaphore) |
| per concurrency group | `TOOL_GROUP_LIMITS` (`{"banking_api": 16}`) | one worker process; **cluster-wide** too when `DISTRIBUTED_TOOL_LIMITS=true` (Redis leases) |
| per tool | `max_concurrency` in tool metadata | one worker process |
| per session | `MAX_SESSION_CONCURRENT_TOOLS` | one worker (a session runs on one worker at a time anyway, because of the session lock) |
| per plan wave | `MAX_PARALLEL_TOOLS` | one turn |

An in-memory semaphore does **not** limit the cluster: with N workers the effective global ceiling is
N × `TOOL_GLOBAL_CONCURRENCY`. For a hard cluster-wide cap on an institution API, enable `DISTRIBUTED_TOOL_LIMITS`: each
call then also holds an expiring lease in a Redis sorted set for its group (`toollimit:<tenant>:<group>`; Lua with the
Redis server clock; leases expire after `timeout + 5 s`, so a crashed worker cannot leak capacity). Slots are always
acquired in the same order and time out with the step's deadline instead of hanging (`concurrency_limit_hits`).

## 9. Horizontal scaling

```
 Caller A ─┐                ┌─► Worker 1 ─┐
 Caller B ─┼─ load balancer ┼─► Worker 2 ─┼─► Redis: session state (versioned) · session locks (renewed) · pub/sub · leases
 Caller C ─┘                └─► Worker 3 ─┘   Postgres: sessions mirror · agent_workflows · tool_executions · audit
                                               Institution APIs (Idempotency-Key)
```

* Any worker can serve any turn: all turn state is in Redis/Postgres; nothing session-specific lives in process memory
  except in-flight submissions (which the session lock waits for).
* **Session lock** (`lock:session:<id>`): Redis `SET NX` + token, TTL `SESSION_LOCK_TTL`, renewed every TTL/3 while the
  turn runs; a crashed worker's lock expires. Waiters give up after `SESSION_LOCK_WAIT` with a polite "busy" reply
  (`session_busy`) instead of queueing forever or interleaving.
* **Versioned saves**: session saves under the lock are compare-and-set on `session:<id>:v` (Lua). If a lock was lost
  (renewal failure, long GC pause) and another worker wrote meanwhile, the stale write is rejected
  (`bfsi_session_state_conflicts_total`) rather than clobbering newer state.
* Production refuses to start without Redis (`REDIS_REQUIRED_IN_PRODUCTION`). `InMemoryStateStore` is for dev/tests and
  is single-process only.

## 10. Voice

Deterministic, neutral acknowledgements are emitted as `workflow.progress` events and spoken by the LiveKit adapter
before the answer: *"Processing your request now. I'll confirm as soon as the bank responds."* (only after policy allowed
the mutation and it is being sent), *"Still checking, one moment."* (reads slower than `SLOW_OPERATION_ACK_SECONDS`),
*"Checking the final status with the bank."* (verifying an ambiguous result). None of them implies success; "done" is
only spoken from a verified result. Parallel reads shorten tool latency for multi-part questions; read-only turns add no
database writes.

## 11. Observability

Per step (structured JSON log `workflow step finished`): `workflow_id, session_id, tool_call_id, step_id, tool_name,
worker_id, execution_status, latency_ms, parallel_group, dependency_count, retry_count, policy_decision,
verification_status, failure_category` — no arguments, results, OTPs or credentials (the existing redaction filter
still applies). Runtime events: `tool.started` (with `step_id`, `parallel_group`), `workflow.progress`,
`verification.completed`.

Prometheus: `bfsi_tool_execution_mode_total{mode=parallel|sequential}`, `bfsi_workflow_outcomes_total{status}`
(success/failure rate), `bfsi_verification_outcomes_total{status}`, `bfsi_tool_timeouts_total`, `bfsi_tool_retries_total`,
`bfsi_tool_failures_total{category}`, `bfsi_financial_duplicate_prevention_total{reason}`, `bfsi_policy_blocks_total`,
`bfsi_concurrency_limit_hits_total{scope}`, `bfsi_workflow_latency_seconds` (average and p95 via `histogram_quantile`),
`bfsi_session_lock_wait_seconds`, `bfsi_session_busy_total`, `bfsi_session_state_conflicts_total`.

## 12. Configuration

| Setting | Default | |
|---|---|---|
| `MAX_AGENT_ITERATIONS` | 4 | LLM ⇄ tools rounds per turn (`LLM_MAX_TOOL_ITERATIONS` still accepted) |
| `MAX_WORKFLOW_STEPS` | 12 | steps per workflow, including template steps |
| `WORKFLOW_TIMEOUT` | 45 s | one execution pass; must exceed `DEFAULT_TOOL_TIMEOUT` |
| `DEFAULT_TOOL_TIMEOUT` | 10 s | when a tool has no timeout |
| `MAX_PARALLEL_TOOLS` | 4 | steps per parallel wave |
| `MAX_SESSION_CONCURRENT_TOOLS` | 4 | per session |
| `TOOL_GLOBAL_CONCURRENCY` | 64 | per worker process |
| `TOOL_GROUP_LIMITS` | `{"banking_api":16,"knowledge":16}` | per concurrency group |
| `DISTRIBUTED_TOOL_LIMITS` | false | also enforce group limits cluster-wide (Redis) |
| `MAX_MUTATIONS_PER_PLAN` | 1 | state changes per request |
| `READ_RETRY_COUNT` / `WRITE_RETRY_COUNT` | 2 / 1 | writes: idempotent + keyed only; financial: never |
| `VERIFICATION_ATTEMPTS` / `VERIFICATION_INTERVAL` | 3 / 0.5 s | bounded status polling |
| `SLOW_OPERATION_ACK_SECONDS` | 1.2 | voice acknowledgement delay |
| `SESSION_LOCK_TTL` / `SESSION_LOCK_WAIT` | 30 s / 20 s | must exceed `DEFAULT_TOOL_TIMEOUT` |
| `REDIS_REQUIRED_IN_PRODUCTION` | true | startup check |
| `LLM_PARALLEL_TOOL_CALLS` | true | let OpenAI-compatible models propose several calls per turn |
| `WORKER_ID` | `hostname:pid` | recorded on workflows and logs |

## 13. Example traces (captured from the test stack)

**1 — parallel reads** — *"What is my balance and my recent transactions?"*

```
tool.started    get_recent_transactions  wave=1 step=s1
tool.started    get_account_balance      wave=1 step=s2      ← same wave: concurrent (with 400 ms injected latency each,
                                                                 the turn finishes in < 750 ms — asserted by the test suite)
tool.completed  get_recent_transactions  wave=1
tool.completed  get_account_balance      wave=1
=> Here are your recent transactions: … Your savings account XXXX7788 has an available balance of ₹12,85,230.75.
```

**2 — dependent transfer**: find_beneficiary → validate → transaction auth → confirmation → execute → verify

```
> "Transfer ₹1,000 to Rahul"
tool.started    find_beneficiary   wave=1 step=s2           (s3 CHECK: exactly one active beneficiary → BEN01, wave 2)
tool.started    transfer_money     wave=3 step=s1           policy: REQUIRE_AUTH (CRITICAL → transaction OTP bound to the action hash)
auth.required   transfer_money
=> For your security, I've sent a one-time password to … XXXXXX3210 … transfer ₹1,000 to Rahul … (Rahul Verma, account XXXX4521)
> "123456"
tool.started    transfer_money     wave=4 step=s1           policy: REQUIRE_CONFIRMATION
=> Please confirm: transfer ₹1,000 to Rahul from your savings account (Rahul Verma, account XXXX4521). …
> "yes"
tool.started            transfer_money  wave=5 step=s1      policy: ALLOW (grant bound to the hash) → write-ahead SUBMITTING
workflow.progress       phase=submitting "Processing your request now. I'll confirm as soon as the bank responds."
workflow.progress       phase=verifying  step=s4            get_transfer_status(idempotency_key)
verification.completed  transfer_money  status=SUCCESS
tool.completed          transfer_money  wave=5               ← receipt only after verification
=> Transfer successful. ₹1,000 has been sent to Rahul Verma (XXXX4521). Transaction reference: IMPSFAEB1681C0.
```

With `timeout_after_commit` injected, the same "yes" produces one bank execution, a TIMEOUT on the step (attempts = 1),
`verification.completed status=SUCCESS` from the status lookup, and the same receipt — no resend.

**3 — Callers A/B/C on Workers 1/2/3** (shared Redis, concurrent)

```
caller A (session 49a4b9a9, CUST1001) -> worker 1: Your savings account XXXX7788 has an available balance of ₹12,84,230.75.
caller B (session 7d0c78b6, CUST1002) -> worker 2: Your savings account XXXX4411 has an available balance of ₹15,200.
caller C (session 1bff60c1, CUST1001) -> worker 3: Your savings account XXXX7788 has an available balance of ₹12,84,230.75.
```

Caller A's transfer started on worker 1, its OTP verified on worker 2 and its confirmation executed on worker 3 —
one execution; caller C (same customer, different session) cannot confirm it
(`tests/integration/test_orchestration.py::test_three_workers_share_sessions_without_leaking`, and the real-Redis/Postgres
variant in `test_real_infrastructure.py`).

## 14. Known limitations

* Workflow templates are code-defined (`execution/templates.py`); tenant-configurable templates are not implemented.
* Verification sources exist for transfers and card blocks; other mutating tools fall back to their explicit response
  and escalate when that is ambiguous.
* `NOT_FOUND` from a status lookup is treated as "not processed" only after the in-flight window
  (tool timeout + grace) and bounded polling; an institution with slower-than-that status propagation should raise
  `VERIFICATION_ATTEMPTS` / `VERIFICATION_INTERVAL`.
* In-process limits are per worker (see §8); cluster-wide limits cover concurrency groups only.
* A session save rejected by the version check (lock lost) is logged and counted, not merged.
* `timeout_seconds` is an integer column: sub-second tool timeouts are only possible in code/tests.
