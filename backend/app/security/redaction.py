"""Redaction profiles for logs, analytics, storage and audit."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Any

from app.security.pii import SECRET_TYPES, PIIDetector, PIIMatch, PIIType


class RedactionProfile(StrEnum):
    LOG = "log"  # logs & traces: mask every PII type incl. amounts
    ANALYTICS = "analytics"  # same strength as LOG; used for metrics/eval exports
    STORAGE = "storage"  # conversation store: strip auth secrets only (record of conversation)
    AUDIT = "audit"  # audit trail: strip secrets, partially mask identifiers, keep amounts


SENSITIVE_KEYS = re.compile(
    r"^(otp|pin|mpin|cvv|cvc|password|passcode|secret|client_secret|api_key|apikey|token|access_token|"
    r"refresh_token|authorization|credentials?|private_key)$",
    re.I,
)
_SECRET_PLACEHOLDER = {
    PIIType.OTP: "[OTP REDACTED]",
    PIIType.PIN: "[PIN REDACTED]",
    PIIType.CVV: "[CVV REDACTED]",
    PIIType.PASSWORD: "[PASSWORD REDACTED]",
}


def _last4(value: str) -> str:
    d = re.sub(r"\W", "", value)
    return d[-4:] if len(d) >= 4 else ""


def mask_value(m: PIIMatch) -> str:
    v = m.value
    match m.type:
        case t if t in SECRET_TYPES:
            return _SECRET_PLACEHOLDER[t]
        case PIIType.CARD_NUMBER:
            return f"XXXX-XXXX-XXXX-{_last4(v)}"
        case PIIType.AADHAAR:
            return f"XXXX XXXX {_last4(v)}"
        case PIIType.ACCOUNT_NUMBER | PIIType.PHONE:
            return f"XXXXXX{_last4(v)}"
        case PIIType.PAN:
            return f"XXXXX{v[5:9]}X" if len(v) == 10 else "[PAN]"
        case PIIType.EMAIL:
            user, _, domain = v.partition("@")
            return f"{user[:1]}***@{domain}"
        case PIIType.UPI_ID:
            user, _, handle = v.partition("@")
            return f"{user[:1]}***@{handle}"
        case _:
            return f"[{m.type.value}]"


class Redactor:
    def __init__(self) -> None:
        self._detector = PIIDetector(include_amounts=False)
        self._detector_amounts = PIIDetector(include_amounts=True)

    def redact_text(self, text: str | None, profile: RedactionProfile = RedactionProfile.LOG) -> str:
        if not text:
            return text or ""
        detector = self._detector_amounts if profile in (RedactionProfile.LOG, RedactionProfile.ANALYTICS) else self._detector
        matches = detector.detect(text)
        if profile == RedactionProfile.STORAGE:
            matches = [m for m in matches if m.type in SECRET_TYPES]
        out, cursor = [], 0
        for m in matches:
            out.append(text[cursor : m.start])
            out.append(mask_value(m))
            cursor = m.end
        out.append(text[cursor:])
        return "".join(out)

    def redact_data(self, data: Any, profile: RedactionProfile = RedactionProfile.LOG) -> Any:
        if isinstance(data, dict):
            return {
                k: ("[REDACTED]" if SENSITIVE_KEYS.match(str(k)) else self.redact_data(v, profile))
                for k, v in data.items()
            }
        if isinstance(data, list | tuple):
            return [self.redact_data(v, profile) for v in data]
        if isinstance(data, str):
            return self.redact_text(data, profile)
        return data

    def strip_secrets(self, text: str) -> str:
        return self.redact_text(text, RedactionProfile.STORAGE)


_default = Redactor()


def redact(text: str | None, profile: RedactionProfile = RedactionProfile.LOG) -> str:
    return _default.redact_text(text, profile)


def redact_data(data: Any, profile: RedactionProfile = RedactionProfile.LOG) -> Any:
    return _default.redact_data(data, profile)


def strip_secrets(text: str) -> str:
    return _default.strip_secrets(text)


class PIIRedactingFilter(logging.Filter):
    """Masks PII in every log record (message, args and `extra` fields) before any handler sees it."""

    _STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the request
            msg = str(record.msg)
        record.msg = _default.redact_text(msg, RedactionProfile.LOG)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in self._STD and not key.startswith("_"):
                record.__dict__[key] = (
                    "[REDACTED]" if SENSITIVE_KEYS.match(key) else _default.redact_data(value, RedactionProfile.LOG)
                )
        return True
