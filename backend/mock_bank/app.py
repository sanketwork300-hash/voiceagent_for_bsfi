"""Mock bank: what an institution would expose to the platform.

* REST + OpenAPI (`/v1/...`, spec at `/openapi.json`) with `x-bfsi-*` governance extensions
* MCP server (`/mcp`, Streamable HTTP, JSON-RPC 2.0) for card and payment operations

The platform never touches this service's data store; it only calls these APIs.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse

from mock_bank import data
from mock_bank.data import DB, MOCK_OTP, OTP_CHALLENGES, mask

API_KEY = "mock-bank-api-key"
app = FastAPI(title="Mock Bank Core API", version="1.0.0",
              description="Sandbox core-banking APIs for the BFSI agent platform prototype.")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(401, "invalid API key")


def _customer(customer_id: str) -> dict:
    c = DB["customers"].get(customer_id)
    if c is None:
        raise HTTPException(404, "customer not found")
    return c


def gov(risk: str, min_auth: str = "FULLY_AUTHENTICATED", confirm: bool = False, internal: bool = False,
        intents: list[str] | None = None, template: str | None = None) -> dict:
    ext: dict[str, Any] = {"x-bfsi-risk-level": risk, "x-bfsi-min-auth": min_auth, "x-bfsi-requires-confirmation": confirm,
                           "x-bfsi-injected": {"customer_id": "customer_id"},
                           # scheduling metadata: read-only, safe to run in parallel, shares the core-banking rate limit
                           "x-bfsi-operation-type": "READ", "x-bfsi-side-effect": "ACCOUNT_READ", "x-bfsi-parallel-safe": True,
                           "x-bfsi-idempotent": True, "x-bfsi-concurrency-group": "banking_api"}
    if internal:
        ext["x-bfsi-internal"] = True
    if intents:
        ext["x-bfsi-intents"] = intents
    if template:
        ext["x-bfsi-confirmation-template"] = template
    return ext


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------------------- fault injection (sandbox only)
async def fault(tool: str, phase: str) -> None:
    """Configured via POST /_admin/faults. phase: "before" (nothing committed yet) or "after" (committed)."""
    f = data.FAULTS.get(tool)
    if not f or f.get("count", 1) <= 0:
        return
    mode = f.get("mode", "")
    if (phase == "before" and mode in ("delay", "timeout_before_commit", "error_500")) or (
            phase == "after" and mode in ("timeout_after_commit", "error_500_after_commit")):
        f["count"] = f.get("count", 1) - 1
        if mode.startswith("error_500"):
            raise HTTPException(500, "injected failure")
        await asyncio.sleep(float(f.get("seconds", 5)))
        if mode == "timeout_before_commit":
            raise HTTPException(504, "injected timeout")


# ------------------------------------------------------------------------------------------- REST
@app.get("/v1/customers/{customer_id}/accounts", operation_id="get_account_balance", dependencies=[Depends(require_key)],
         summary="Get the customer's account balances", openapi_extra=gov("MEDIUM"),
         description="Retrieve the customer's deposit accounts with available and ledger balances.")
async def accounts(customer_id: str) -> dict:
    _customer(customer_id)
    await fault("get_account_balance", "before")
    return {"accounts": [{"account_type": a["account_type"], "account_number_masked": mask(a["account_number"]),
                          "available_balance": a["available_balance"], "ledger_balance": a["ledger_balance"],
                          "currency": a["currency"]} for a in DB["accounts"][customer_id]]}


@app.get("/v1/customers/{customer_id}/transactions", operation_id="get_recent_transactions", dependencies=[Depends(require_key)],
         summary="Recent transactions", openapi_extra=gov("MEDIUM"),
         description="Retrieve the customer's most recent account transactions (newest first).")
async def transactions(customer_id: str, limit: int = Query(5, ge=1, le=20, description="Number of transactions")) -> dict:
    _customer(customer_id)
    await fault("get_recent_transactions", "before")
    return {"transactions": DB["transactions"][customer_id][:limit]}


@app.get("/v1/customers/{customer_id}/loans", operation_id="get_loan_details", dependencies=[Depends(require_key)],
         summary="Loan details", openapi_extra=gov("MEDIUM"),
         description="Retrieve the customer's loans: outstanding balance, EMI, next due date, rate and remaining tenure.")
async def loans(customer_id: str) -> dict:
    _customer(customer_id)
    await fault("get_loan_details", "before")
    return {"loans": [{**{k: v for k, v in ln.items() if k != "loan_account"}, "loan_account_masked": mask(ln["loan_account"])}
                      for ln in DB["loans"][customer_id]]}


@app.get("/v1/customers/{customer_id}/payments/status", operation_id="get_payment_status", dependencies=[Depends(require_key)],
         summary="Payment status", openapi_extra=gov("MEDIUM"),
         description="Status of a payment by reference (UPI/NEFT/IMPS). Without a reference, returns the latest payment.")
async def payment_status(customer_id: str, payment_ref: str | None = Query(None, description="Payment reference, e.g. UPI4815162342")) -> dict:
    _customer(customer_id)
    pays = DB["payments"][customer_id]
    if payment_ref:
        found = next((p for p in pays if p["payment_ref"] == payment_ref.upper()), None)
        if found is None:
            raise HTTPException(404, "payment reference not found")
        return found
    if not pays:
        raise HTTPException(404, "no payments found")
    return sorted(pays, key=lambda p: p["updated_at"])[-1]


# --- authentication (internal tools: called by the platform, never by the LLM) ---
@app.post("/v1/auth/lookup", operation_id="lookup_customer", dependencies=[Depends(require_key)],
          openapi_extra={"x-bfsi-internal": True, "x-bfsi-risk-level": "LOW", "x-bfsi-min-auth": "UNAUTHENTICATED"})
async def lookup(body: dict = Body(..., examples=[{"phone": "9876543210"}])) -> dict:
    phone, ref = body.get("phone"), body.get("customer_ref")
    for c in DB["customers"].values():
        if (phone and c["phone"] == phone[-10:]) or (ref and c["customer_id"] == ref):
            return {"customer_id": c["customer_id"], "phone_masked": "XXXXXX" + c["phone"][-4:]}
    raise HTTPException(404, "customer not found")


@app.post("/v1/customers/{customer_id}/otp", operation_id="send_otp", dependencies=[Depends(require_key)],
          openapi_extra={"x-bfsi-internal": True, "x-bfsi-risk-level": "LOW", "x-bfsi-min-auth": "IDENTIFIED",
                         "x-bfsi-injected": {"customer_id": "customer_id"}})
async def send_otp(customer_id: str, body: dict = Body(default={})) -> dict:
    c = _customer(customer_id)
    cid = "CH-" + uuid.uuid4().hex[:12]
    OTP_CHALLENGES[cid] = {"customer_id": customer_id, "otp": MOCK_OTP, "purpose": body.get("purpose", "login"),
                           "expires": datetime.now(UTC) + timedelta(minutes=5), "attempts": 0}
    return {"challenge_id": cid, "destination_masked": "XXXXXX" + c["phone"][-4:], "expires_in": 300}


@app.post("/v1/otp/verify", operation_id="verify_otp", dependencies=[Depends(require_key)],
          openapi_extra={"x-bfsi-internal": True, "x-bfsi-risk-level": "LOW", "x-bfsi-min-auth": "IDENTIFIED",
                         "x-bfsi-injected": {"customer_id": "customer_id"}})
async def verify_otp(body: dict = Body(...)) -> dict:
    ch = OTP_CHALLENGES.get(body.get("challenge_id", ""))
    if ch is None or ch["expires"] < datetime.now(UTC) or ch["customer_id"] != body.get("customer_id"):
        return {"verified": False, "reason": "invalid_or_expired"}
    ch["attempts"] += 1
    if ch["attempts"] > 3:
        OTP_CHALLENGES.pop(body["challenge_id"], None)
        return {"verified": False, "reason": "too_many_attempts"}
    if secrets.compare_digest(str(body.get("otp", "")), ch["otp"]):
        OTP_CHALLENGES.pop(body["challenge_id"], None)
        return {"verified": True}
    return {"verified": False, "reason": "mismatch"}


# -------------------------------------------------------------------------------------------- MCP
BANKING_GROUP = "banking_api"
MCP_TOOLS = [
    {
        "name": "get_card_status",
        "description": "Retrieve the customer's cards: status, and for credit cards the outstanding amount, minimum due, due date and available limit.",
        "inputSchema": {"type": "object", "properties": {
            "customer_id": {"type": "string"},
            "card_type": {"type": "string", "enum": ["credit", "debit"], "description": "Optional filter"}},
            "required": ["customer_id"]},
        "annotations": {"title": "Card status", "readOnlyHint": True},
        "_meta": {"bfsi": {"risk_level": "MEDIUM", "min_auth_state": "FULLY_AUTHENTICATED",
                           "execution": {"operation_type": "READ", "side_effect": "ACCOUNT_READ", "parallel_safe": True,
                                         "idempotent": True, "concurrency_group": BANKING_GROUP}}},
    },
    {
        "name": "find_beneficiary",
        "description": "Look up the customer's registered beneficiaries by (partial) name.",
        "inputSchema": {"type": "object", "properties": {
            "customer_id": {"type": "string"},
            "name": {"type": "string", "description": "Beneficiary name or part of it"}},
            "required": ["customer_id", "name"]},
        "annotations": {"title": "Find beneficiary", "readOnlyHint": True},
        "_meta": {"bfsi": {"risk_level": "MEDIUM", "min_auth_state": "FULLY_AUTHENTICATED",
                           "intents": ["ACTION_REQUEST", "CUSTOMER_DATA_QUERY"],
                           "execution": {"operation_type": "READ", "side_effect": "ACCOUNT_READ", "parallel_safe": True,
                                         "idempotent": True, "concurrency_group": BANKING_GROUP}}},
    },
    {
        "name": "block_card",
        "description": "Permanently block one of the customer's cards (lost, stolen or suspected fraud). Irreversible.",
        "inputSchema": {"type": "object", "properties": {
            "customer_id": {"type": "string"},
            "card_type": {"type": "string", "enum": ["credit", "debit"]},
            "reason": {"type": "string", "enum": ["lost", "stolen", "fraud", "customer_request"]}},
            "required": ["customer_id", "card_type"]},
        "annotations": {"title": "Block card", "readOnlyHint": False, "destructiveHint": True},
        "_meta": {"bfsi": {"risk_level": "HIGH", "min_auth_state": "FULLY_AUTHENTICATED", "requires_confirmation": True,
                           "intents": ["ACTION_REQUEST", "FRAUD_REQUEST"],
                           "confirmation_template": "permanently block your {card_type} card",
                           "execution": {"operation_type": "WRITE", "side_effect": "ACCOUNT_MUTATION", "idempotent": True,
                                         "concurrency_group": BANKING_GROUP}}},
    },
    {
        "name": "transfer_money",
        "description": "Transfer money from the customer's savings account to one of their registered beneficiaries.",
        "inputSchema": {"type": "object", "properties": {
            "customer_id": {"type": "string"},
            "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 10000000, "description": "Amount in INR"},
            "payee_name": {"type": "string", "description": "Registered beneficiary name"},
            "beneficiary_id": {"type": "string", "description": "Resolved beneficiary (set by the platform)"},
            "currency": {"type": "string", "enum": ["INR"]},
            "remarks": {"type": "string", "maxLength": 50}},
            "required": ["customer_id", "amount", "payee_name"]},
        "annotations": {"title": "Transfer money", "readOnlyHint": False, "destructiveHint": True},
        "_meta": {"bfsi": {"risk_level": "CRITICAL", "min_auth_state": "TRANSACTION_AUTHENTICATED", "requires_confirmation": True,
                           "intents": ["ACTION_REQUEST"],
                           "confirmation_template": "transfer {amount_inr} to {payee_name} from your savings account",
                           # idempotent at the bank (keyed by _meta.idempotency_key) — the platform still never retries it
                           "execution": {"operation_type": "WRITE", "side_effect": "FINANCIAL_MUTATION", "idempotent": True,
                                         "concurrency_group": BANKING_GROUP}}},
    },
    {
        "name": "get_transfer_status",
        "description": "Status of a transfer request by its idempotency key (platform verification only).",
        "inputSchema": {"type": "object", "properties": {
            "customer_id": {"type": "string"}, "idempotency_key": {"type": "string"}},
            "required": ["customer_id", "idempotency_key"]},
        "annotations": {"title": "Transfer status", "readOnlyHint": True},
        "_meta": {"bfsi": {"risk_level": "MEDIUM", "min_auth_state": "FULLY_AUTHENTICATED", "internal": True,
                           "execution": {"operation_type": "VERIFY", "side_effect": "ACCOUNT_READ", "parallel_safe": True,
                                         "idempotent": True, "concurrency_group": BANKING_GROUP}}},
    },
]
_SESSIONS: set[str] = set()


def _get_card_status(args: dict) -> dict:
    cards = DB["cards"].get(args["customer_id"])
    if cards is None:
        raise ValueError("customer not found")
    out = []
    for c in cards:
        if args.get("card_type") and c["card_type"] != args["card_type"]:
            continue
        view = {k: v for k, v in c.items() if k not in ("card_number", "card_id", "linked_account")}
        view["card_number_masked"] = mask(c["card_number"])
        out.append(view)
    return {"cards": out}


def _block_card(args: dict) -> dict:
    cards = DB["cards"].get(args["customer_id"]) or []
    matches = [c for c in cards if c["card_type"] == args["card_type"] and c["status"] == "ACTIVE"]
    if not matches:
        raise ValueError(f"no active {args['card_type']} card found")
    card = matches[0]
    card["status"] = "BLOCKED"
    return {"status": "BLOCKED", "card_type": card["card_type"], "card_number_masked": mask(card["card_number"]),
            "reference": "BLK" + uuid.uuid4().hex[:8].upper(), "blocked_at": datetime.now(UTC).isoformat()}


def _find_beneficiary(args: dict) -> dict:
    name = str(args.get("name", "")).strip().lower()
    if not name:
        raise ValueError("name is required")
    bens = [b for b in DB["beneficiaries"].get(args["customer_id"], [])
            if b["name"].lower().startswith(name) or name in b["name"].lower()]
    return {"beneficiaries": [{"beneficiary_id": b["beneficiary_id"], "name": b["name"], "status": b.get("status", "ACTIVE"),
                               "account_number_masked": mask(b["account_number"])} for b in bens]}


def _transfer(args: dict) -> dict:
    cid = args["customer_id"]
    if args.get("beneficiary_id"):
        bens = [b for b in DB["beneficiaries"].get(cid, []) if b["beneficiary_id"] == args["beneficiary_id"]]
        if not bens:
            raise ValueError("beneficiary not found")
        name = str(args.get("payee_name", "")).strip().lower()
        if name and not (bens[0]["name"].lower().startswith(name) or name in bens[0]["name"].lower()):
            raise ValueError("payee name does not match the beneficiary")
    else:
        name = args["payee_name"].strip().lower()
        bens = [b for b in DB["beneficiaries"].get(cid, []) if b["name"].lower().startswith(name) or name in b["name"].lower()]
        if not bens:
            raise ValueError(f"no registered beneficiary matching '{args['payee_name']}'")
        if len(bens) > 1:
            raise ValueError("more than one beneficiary matches; please specify the full name")
    acct = DB["accounts"][cid][0]
    amount = float(args["amount"])
    if amount > acct["available_balance"]:
        raise ValueError("insufficient balance")
    acct["available_balance"] = round(acct["available_balance"] - amount, 2)
    ref = "IMPS" + uuid.uuid4().hex[:10].upper()
    DB["payments"][cid].append({"payment_ref": ref, "amount": amount, "payee": bens[0]["name"], "status": "SUCCESS",
                                "updated_at": datetime.now(UTC).date().isoformat()})
    DB["transfer_executions"] += 1
    return {"status": "SUCCESS", "transaction_ref": ref, "amount": amount, "payee_name": bens[0]["name"],
            "beneficiary_id": bens[0]["beneficiary_id"], "payee_account_masked": mask(bens[0]["account_number"]),
            "balance_after": acct["available_balance"]}


def _transfer_status(args: dict) -> dict:
    rec = data.IDEMPOTENCY.get(f"transfer_money:{args.get('idempotency_key', '')}")
    if rec is None or rec["customer_id"] != args["customer_id"]:
        raise LookupError("no transfer with that idempotency key")
    return rec["result"]


HANDLERS = {"get_card_status": _get_card_status, "find_beneficiary": _find_beneficiary, "block_card": _block_card,
            "transfer_money": _transfer, "get_transfer_status": _transfer_status}
IDEMPOTENT_WRITES = {"block_card", "transfer_money"}


def _call(name: str, args: dict, meta: dict) -> dict:
    """Writes are de-duplicated by the caller's idempotency key: a repeat returns the original result."""
    key = meta.get("idempotency_key")
    if name in IDEMPOTENT_WRITES and key:
        rec = data.IDEMPOTENCY.get(f"{name}:{key}")
        if rec is not None:
            if rec["customer_id"] != args.get("customer_id"):
                raise ValueError("idempotency key reused for a different customer")
            DB["duplicates_prevented"] += 1
            return rec["result"]
        result = HANDLERS[name](args)
        data.IDEMPOTENCY[f"{name}:{key}"] = {"customer_id": args.get("customer_id"), "result": result}
        return result
    return HANDLERS[name](args)


def _rpc(id_: Any, result: dict | None = None, error: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": id_, **({"error": error} if error else {"result": result})}


@app.post("/mcp", include_in_schema=False)
async def mcp(request: Request, x_api_key: str | None = Header(default=None), mcp_session_id: str | None = Header(default=None)) -> Response:
    if x_api_key != API_KEY:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    msg = await request.json()
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification
        return Response(status_code=202)
    if method == "initialize":
        sid = uuid.uuid4().hex
        _SESSIONS.add(sid)
        return JSONResponse(_rpc(mid, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                                       "capabilities": {"tools": {"listChanged": False}},
                                       "serverInfo": {"name": "mock-bank-mcp", "version": "1.0.0"}}),
                            headers={"Mcp-Session-Id": sid})
    if mcp_session_id not in _SESSIONS:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    if method == "tools/list":
        return JSONResponse(_rpc(mid, {"tools": MCP_TOOLS}))
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        if name not in HANDLERS:
            return JSONResponse(_rpc(mid, error={"code": -32602, "message": "unknown tool"}))
        await fault(name, "before")
        try:
            result = _call(name, params.get("arguments") or {}, params.get("_meta") or {})
        except LookupError as e:
            err = {"error": str(e), "code": "NOT_FOUND"}
            return JSONResponse(_rpc(mid, {"content": [{"type": "text", "text": json.dumps(err)}],
                                           "structuredContent": err, "isError": True}))
        except (ValueError, KeyError) as e:
            return JSONResponse(_rpc(mid, {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                                           "structuredContent": {"error": str(e)}, "isError": True}))
        await fault(name, "after")  # e.g. committed, then the response is lost (timeout_after_commit)
        return JSONResponse(_rpc(mid, {"content": [{"type": "text", "text": json.dumps(result)}],
                                       "structuredContent": result, "isError": False}))
    if method == "ping":
        return JSONResponse(_rpc(mid, {}))
    return JSONResponse(_rpc(mid, error={"code": -32601, "message": "method not found"}))


@app.delete("/mcp", include_in_schema=False)
async def mcp_close(mcp_session_id: str | None = Header(default=None)) -> Response:
    _SESSIONS.discard(mcp_session_id or "")
    return Response(status_code=204)


@app.post("/_admin/reset", include_in_schema=False)
async def admin_reset() -> dict:
    data.reset()
    return {"reset": True}


@app.post("/_admin/faults", include_in_schema=False)
async def admin_faults(body: dict = Body(...)) -> dict:
    """{"tool": "transfer_money", "mode": "timeout_after_commit|timeout_before_commit|delay|error_500|error_500_after_commit",
    "seconds": 5, "count": 1}  — or {"clear": true}."""
    if body.get("clear"):
        data.FAULTS.clear()
    else:
        data.FAULTS[body["tool"]] = {"mode": body.get("mode", "delay"), "seconds": body.get("seconds", 5), "count": body.get("count", 1)}
    return {"faults": data.FAULTS}


@app.get("/_admin/stats", include_in_schema=False)
async def admin_stats() -> dict:
    return {"transfer_executions": DB["transfer_executions"], "duplicates_prevented": DB["duplicates_prevented"]}

