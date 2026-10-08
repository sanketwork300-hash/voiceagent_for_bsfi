"""Failure taxonomy and retry rules for tool calls.

Every failed tool call gets a `FailureCategory` (what went wrong) and a `Disposition` (what may be done about it).
The disposition depends on the tool's trusted execution metadata, never on LLM output:

  READ                      retryable failures -> bounded exponential backoff with jitter
  idempotent WRITE          retried only when the institution de-duplicates by our Idempotency-Key
  non-idempotent / FINANCIAL never retried; an ambiguous outcome (timeout, 5xx, lost connection after send)
                            REQUIRES_VERIFICATION: ask the system of record what happened before doing anything else
"""

from __future__ import annotations

import random
from enum import StrEnum

from app.tools.schemas import ExecutionMetadata, SideEffect


class FailureCategory(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    BUSINESS_RULE_FAILURE = "BUSINESS_RULE_FAILURE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DUPLICATE = "DUPLICATE"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    UNKNOWN = "UNKNOWN"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    POLICY_BLOCKED = "POLICY_BLOCKED"  # not an institution failure: the policy engine did not allow the call


class Disposition(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    REQUIRES_VERIFICATION = "requires_verification"
    REQUIRES_HUMAN = "requires_human"


# Categories where the request may have reached the institution and been processed.
AMBIGUOUS = frozenset({FailureCategory.TIMEOUT, FailureCategory.UNKNOWN, FailureCategory.DEPENDENCY_UNAVAILABLE,
                       FailureCategory.CONFLICT, FailureCategory.DUPLICATE})
_TRANSIENT = frozenset({FailureCategory.RATE_LIMIT, FailureCategory.TIMEOUT, FailureCategory.NETWORK_ERROR,
                        FailureCategory.DEPENDENCY_UNAVAILABLE, FailureCategory.UNKNOWN})


def category_for_status(status: int) -> FailureCategory:
    if status == 400 or status == 422:
        return FailureCategory.BUSINESS_RULE_FAILURE
    return {401: FailureCategory.AUTHENTICATION_ERROR, 403: FailureCategory.AUTHORIZATION_ERROR,
            404: FailureCategory.NOT_FOUND, 409: FailureCategory.CONFLICT, 429: FailureCategory.RATE_LIMIT,
            502: FailureCategory.DEPENDENCY_UNAVAILABLE, 503: FailureCategory.DEPENDENCY_UNAVAILABLE,
            504: FailureCategory.TIMEOUT}.get(status, FailureCategory.UNKNOWN if status >= 500 else FailureCategory.BUSINESS_RULE_FAILURE)


def disposition(category: FailureCategory, ex: ExecutionMetadata, *, sent: bool | None = None) -> Disposition:
    """`sent=False` means the request provably never left (connection refused): safe to retry even for writes."""
    if category == FailureCategory.PARTIAL_FAILURE:
        return Disposition.REQUIRES_HUMAN
    if category not in _TRANSIENT:
        if ex.mutates and category in AMBIGUOUS:
            return Disposition.REQUIRES_VERIFICATION
        return Disposition.NON_RETRYABLE
    if not ex.mutates:
        return Disposition.RETRYABLE
    if sent is False or category == FailureCategory.RATE_LIMIT:
        # nothing was processed; still only idempotent writes are retried automatically
        return Disposition.RETRYABLE if ex.idempotent else Disposition.NON_RETRYABLE
    return Disposition.REQUIRES_VERIFICATION


def may_retry(category: FailureCategory, ex: ExecutionMetadata, *, sent: bool | None, has_idempotency_key: bool) -> bool:
    """Automatic retry decision. Financial mutations are never retried automatically, whatever the error."""
    if ex.side_effect == SideEffect.FINANCIAL_MUTATION:
        return False
    d = disposition(category, ex, sent=sent)
    if d == Disposition.RETRYABLE:
        return True
    # an idempotent write with a key the institution de-duplicates on can be safely re-sent after an ambiguous failure
    return d == Disposition.REQUIRES_VERIFICATION and ex.idempotent and has_idempotency_key and category in _TRANSIENT


def backoff_delay(attempt: int, *, base: float = 0.2, cap: float = 2.0, retry_after: float | None = None) -> float:
    """Exponential backoff with full jitter; a server-provided Retry-After wins (bounded by `cap` * 5)."""
    if retry_after is not None:
        return min(max(retry_after, 0.0), cap * 5)
    return random.uniform(0, min(cap, base * (2 ** attempt)))  # noqa: S311 - jitter, not crypto
