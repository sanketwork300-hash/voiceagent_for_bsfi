"""Post-execution verification: ask the system of record what actually happened.

Rules (financial safety):
* Verification is read-only — `ToolGateway.verify` refuses mutating tools — and is never a retry of the action.
* After an ambiguous failure (timeout, 5xx, lost connection) the original request is looked up by its idempotency key.
  Found -> SUCCESS (recovered; the customer gets the real receipt). Not found after bounded polling and after the
  in-flight window -> FAILED ("not processed"). Lookup itself unavailable -> UNKNOWN/TIMEOUT -> escalate to a human.
* A record that disagrees with what the customer confirmed (amount / beneficiary) -> PARTIAL -> escalate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from app.agents.execution.models import ExecutionStep, StepStatus, VerificationResult, VerificationStatus
from app.agents.execution.templates import VerifySpec
from app.domain import utcnow
from app.observability import metrics
from app.tools.failures import FailureCategory
from app.tools.schemas import ToolResult

Lookup = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


def _eval_transfer(step: ExecutionStep, res: ToolResult) -> VerificationResult:
    d = res.data if isinstance(res.data, dict) else {}
    status = str(d.get("status", "")).upper()
    ref = d.get("transaction_ref") or d.get("reference")
    evidence = {k: d.get(k) for k in ("status", "transaction_ref", "amount", "beneficiary_id", "payee_name", "payee_account_masked")
                if k in d}
    if status in ("SUCCESS", "COMPLETED"):
        args = step.all_arguments
        amount_ok = d.get("amount") is None or abs(float(d["amount"]) - float(args.get("amount", 0))) < 0.005
        ben_ok = not args.get("beneficiary_id") or d.get("beneficiary_id") in (None, args["beneficiary_id"])
        if not (amount_ok and ben_ok):
            return VerificationResult(status=VerificationStatus.PARTIAL, method="status_lookup", reference=ref, evidence=evidence,
                                      detail="processed transaction differs from the confirmed request")
        return VerificationResult(status=VerificationStatus.SUCCESS, method="status_lookup", reference=ref, evidence=evidence)
    if status in ("FAILED", "REJECTED", "REVERSED"):
        return VerificationResult(status=VerificationStatus.FAILED, method="status_lookup", reference=ref, evidence=evidence,
                                  detail=str(d.get("reason") or "rejected by the bank"))
    return VerificationResult(status=VerificationStatus.UNKNOWN, method="status_lookup", reference=ref, evidence=evidence,
                              detail="still processing" if status in ("PENDING", "PROCESSING") else "unrecognised status")


def _eval_card_blocked(step: ExecutionStep, res: ToolResult) -> VerificationResult:
    cards = (res.data or {}).get("cards", []) if isinstance(res.data, dict) else []
    want = step.arguments.get("card_type")
    relevant = [c for c in cards if not want or c.get("card_type") == want]
    statuses = {str(c.get("status", "")).upper() for c in relevant}
    evidence = {"card_statuses": sorted(statuses)}
    if relevant and "BLOCKED" in statuses:
        return VerificationResult(status=VerificationStatus.SUCCESS, method="state_check", evidence=evidence)
    if relevant and statuses == {"ACTIVE"}:
        return VerificationResult(status=VerificationStatus.FAILED, method="state_check", evidence=evidence,
                                  detail="card is still active")
    return VerificationResult(status=VerificationStatus.UNKNOWN, method="state_check", evidence=evidence)


EVALUATORS: dict[str, Callable[[ExecutionStep, ToolResult], VerificationResult]] = {
    "transfer_status": _eval_transfer, "card_blocked": _eval_card_blocked}


class Verifier:
    def __init__(self, *, attempts: int = 3, interval: float = 0.5, inflight_grace: float = 2.0) -> None:
        self.attempts = attempts
        self.interval = interval
        self.inflight_grace = inflight_grace

    async def verify(self, mutation: ExecutionStep, spec: VerifySpec | None, lookup: Lookup) -> VerificationResult:
        result = await self._verify(mutation, spec, lookup)
        metrics.verification_outcomes.labels(mutation.tool or "-", result.status.value).inc()
        return result

    async def _verify(self, m: ExecutionStep, spec: VerifySpec | None, lookup: Lookup) -> VerificationResult:
        r = m.result
        if spec is None:
            # no independent status source: an explicit success response is the evidence; anything else is unknown
            if m.status == StepStatus.COMPLETED and r and r.ok:
                return VerificationResult(status=VerificationStatus.SUCCESS, method="response")
            if r and r.failure_kind in ("rejected", "not_executed"):
                return VerificationResult(status=VerificationStatus.FAILED, method="response", detail=r.error)
            return VerificationResult(status=VerificationStatus.UNKNOWN, method="response", detail="no status source to verify against")
        if r and not r.ok and r.failure_kind in ("rejected", "not_executed"):
            # the institution explicitly declined (or it was never sent): nothing to look up
            return VerificationResult(status=VerificationStatus.FAILED, method="response", detail=r.error)
        if spec.tool == "get_transfer_status" and not m.idempotency_key:
            return VerificationResult(status=VerificationStatus.UNKNOWN, method="status_lookup", detail="no idempotency key")
        evaluate = EVALUATORS[spec.evaluate]
        args = spec.args(m)
        last: VerificationResult | None = None
        for attempt in range(1, self.attempts + 1):
            res = await lookup(spec.tool, args)
            if res.ok:
                last = evaluate(m, res)
                last.tool, last.attempts = spec.tool, attempt
                if last.status != VerificationStatus.UNKNOWN:
                    return last
            elif res.failure_category == FailureCategory.NOT_FOUND:
                # no record (yet). Only "not processed" once the request can no longer be in flight.
                window_over = m.submitted_at is None or utcnow() > m.submitted_at + timedelta(
                    seconds=(m.timeout_seconds or 10) + self.inflight_grace)
                last = VerificationResult(status=VerificationStatus.FAILED if window_over else VerificationStatus.UNKNOWN,
                                          method="status_lookup", tool=spec.tool, attempts=attempt,
                                          detail="no record of the request at the bank")
            else:
                status = VerificationStatus.TIMEOUT if res.failure_category == FailureCategory.TIMEOUT else VerificationStatus.UNKNOWN
                last = VerificationResult(status=status, method="status_lookup", tool=spec.tool, attempts=attempt,
                                          detail=res.error)
            if attempt < self.attempts:
                await asyncio.sleep(self.interval * attempt)
        assert last is not None
        return last
