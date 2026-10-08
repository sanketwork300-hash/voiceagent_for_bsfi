# Onboarding an institution's systems

The platform never connects to an institution's database. It consumes their APIs.

## OpenAPI / REST

1. `POST /integrations` with `kind=openapi`, `base_url`, `auth_type` (`api_key` | `bearer` | `oauth2_client_credentials`)
   and `credentials` (encrypted at rest; `env:NAME` references allowed).
2. `POST /integrations/{id}/import-openapi` (fetches `{base_url}/openapi.json` or accepts `spec`). Each operation
   becomes a tool named after its `operationId`. Imported tools start **disabled** unless `enable=true`.
3. Review and adjust governance with `PATCH /tools/{name}`; test with `POST /tools/{name}/test`.

Governance extensions on an operation (conservative defaults if absent: GET → MEDIUM, writes → HIGH + confirmation):

```yaml
x-bfsi-risk-level: CRITICAL                 # LOW | MEDIUM | HIGH | CRITICAL
x-bfsi-min-auth: TRANSACTION_AUTHENTICATED  # minimum AuthState
x-bfsi-requires-confirmation: true
x-bfsi-injected: {customer_id: customer_id} # filled from the session; hidden from the LLM
x-bfsi-intents: [ACTION_REQUEST]
x-bfsi-confirmation-template: "transfer {amount_inr} to {payee_name}"
x-bfsi-internal: true                       # platform-only (OTP send/verify, customer lookup)
x-bfsi-tool: false                          # don't expose this operation
# scheduling (docs/orchestration.md) — merged conservatively: a POST can never become a parallel read
x-bfsi-operation-type: WRITE                # READ | WRITE | VERIFY
x-bfsi-side-effect: FINANCIAL_MUTATION      # NONE | ACCOUNT_READ | ACCOUNT_MUTATION | FINANCIAL_MUTATION | EXTERNAL_SIDE_EFFECT
x-bfsi-parallel-safe: false                 # reads only; mutations are always serialized
x-bfsi-idempotent: true                     # the API de-duplicates by Idempotency-Key
x-bfsi-concurrency-group: banking_api       # shared rate-limit bucket (TOOL_GROUP_LIMITS)
x-bfsi-depends-on: [find_beneficiary]       # must complete first when both are in one plan
x-bfsi-max-concurrency: 8                   # per-tool cap (per worker)
```

**Idempotency contract.** Every write carries a deterministic key — REST header `Idempotency-Key`, MCP
`params._meta.idempotency_key` — stable for one workflow step and exact action. The institution should return the
original result for a repeated key. For financial writes also expose a read-only status lookup by that key (the mock
bank's `get_transfer_status`, `x-bfsi-internal`), which the platform uses to verify ambiguous outcomes instead of retrying.

Authentication hooks the platform expects (mark `x-bfsi-internal: true`): `lookup_customer` (phone/ref →
`customer_id`, `phone_masked`), `send_otp` (→ `challenge_id`, `destination_masked`), `verify_otp`
(`challenge_id`, `otp` → `verified`). See `mock_bank/app.py` for a reference implementation.

## MCP

`POST /mcp/servers` (`url` for Streamable HTTP, or `transport=stdio` + `command`; `integration_id` supplies
headers/credentials). Tools are discovered with `tools/list`; governance comes from MCP annotations
(`readOnlyHint`, `destructiveHint`) and optional `_meta.bfsi` (`risk_level`, `min_auth_state`,
`requires_confirmation`, `injected_params`, `intents`, `confirmation_template`, `internal`, and `execution` with the
scheduling fields above, e.g. `{"operation_type": "WRITE", "side_effect": "FINANCIAL_MUTATION", "idempotent": true,
"concurrency_group": "banking_api"}`). A server cannot declare its
own tools below MEDIUM risk. Re-discover with `POST /mcp/servers/{id}/discover` (admin governance edits are kept).

## Custom adapters

For SOAP / ISO 8583 / MQ / mainframe gateways, subclass `app/tools/adapters/base.py:BankAdapter`, register it
with `@register_adapter`, create an integration with `kind=adapter` and `config.adapter=<name>`. Adapter
operations are governed and gated exactly like REST/MCP tools.

## Policies

Tenant rules (`POST /policies`) layer on the defaults in `app/policies/rules.py`. Example — no large transfers by phone:

```json
{"name": "No voice transfers above 2L", "effect": "DENY", "priority": 5,
 "conditions": {"tool": "transfer_money", "channel": "voice", "args": {"amount": {"gt": 200000}}},
 "params": {"reason": "Large transfers are not available over the phone."}}
```

Effects: `DENY`, `REQUIRE_AUTH` (`params.required_auth_state`), `REQUIRE_CONFIRMATION`, `REQUIRE_HUMAN_APPROVAL`,
`SET_MIN_AUTH` (relax/raise the minimum auth for matching calls, e.g. protective card block during fraud).
Override a default by reusing its `id`, or disable it with `is_enabled=false`.
