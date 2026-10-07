"""PII / secret detection tuned for Indian BFSI conversations (chat text and STT transcripts)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class PIIType(StrEnum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    CARD_NUMBER = "CARD_NUMBER"
    CVV = "CVV"
    OTP = "OTP"
    PIN = "PIN"
    PASSWORD = "PASSWORD"
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    IFSC = "IFSC"
    UPI_ID = "UPI_ID"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DOB = "DOB"
    ADDRESS = "ADDRESS"
    AMOUNT = "AMOUNT"


# Authentication secrets: never logged, never persisted, never sent to the LLM.
SECRET_TYPES = frozenset({PIIType.OTP, PIIType.PIN, PIIType.CVV, PIIType.PASSWORD})


@dataclass(frozen=True, slots=True)
class PIIMatch:
    type: PIIType
    start: int
    end: int
    value: str


# --- checksum validators -------------------------------------------------------------------------

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# --- patterns --------------------------------------------------------------------------------------

_SECRET_KW = {
    PIIType.OTP: r"(?:otp|one[\s-]?time[\s-]?password|verification\s+code|ओटीपी)",
    PIIType.PIN: r"(?:m?pin|atm\s+pin|पिन)",
    PIIType.CVV: r"(?:cvv2?|cvc|card\s+verification(?:\s+value)?)",
}
_SECRET_PATTERNS = {
    t: re.compile(rf"\b{kw}\b\s*(?:is|hai|:|-|=|number|no\.?)?\s*(?:is|hai)?\s*(\d(?:[\s-]?\d){{2,7}})\b", re.I)
    for t, kw in _SECRET_KW.items()
}
_PASSWORD = re.compile(r"\b(?:password|passcode|pwd)\b\s*(?:is|:|=)?\s*(\S{4,})", re.I)

_AADHAAR = re.compile(r"(?<![\w-])([2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4})(?![\w-])")
_CARD = re.compile(r"(?<![\w-])(\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,7})(?![\w-])")
_PAN = re.compile(r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b")
_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_UPI = re.compile(r"\b([A-Za-z0-9._-]{2,}@(?:ok)?[a-z]{2,15})\b(?!\.)")
_PHONE = re.compile(r"(?<![\w+-])((?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5})(?![\w-])")
_ACCOUNT = re.compile(r"(?<![\w-])(\d{9,18})(?![\w-])")
_ACCOUNT_CTX = re.compile(r"\b(?:a/?c|account|acct|khata)\b(?:\s+(?:no\.?|number|num))?\s*(?:is|:|-)?\s*(\d{6,18})\b", re.I)
_DATE = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4})"
_DOB = re.compile(rf"\b(?:dob|d\.o\.b|date\s+of\s+birth|born\s+on|birth\s*date|janam\s+tithi)\b\D{{0,12}}{_DATE}", re.I)
_ADDRESS = re.compile(
    r"\b(?:address|pata|residing\s+at|live\s+at)\b\s*(?:is|hai|:)?\s*([^.\n]{8,120}?(?:\b\d{6}\b|$))", re.I
)
_PINCODE_ADDR = re.compile(
    r"\b((?:flat|house|plot|h\.?\s?no\.?|#)\s*[\w/-]+[^.\n]{5,100}?\b(?:road|rd|nagar|street|marg|colony|sector|lane)\b[^.\n]{0,60}?\b\d{6}\b)",
    re.I,
)
_AMOUNT = re.compile(
    r"((?:₹|rs\.?|inr)\s?\d[\d,]*(?:\.\d{1,2})?(?:\s?(?:lakh|lakhs|crore|cr|k))?|\d[\d,]*(?:\.\d{1,2})?\s?(?:rupees|rupaye|rupay|lakh|crore))",
    re.I,
)

_PRIORITY = [
    PIIType.PASSWORD, PIIType.OTP, PIIType.PIN, PIIType.CVV, PIIType.CARD_NUMBER, PIIType.AADHAAR,
    PIIType.PAN, PIIType.IFSC, PIIType.EMAIL, PIIType.UPI_ID, PIIType.DOB, PIIType.ADDRESS,
    PIIType.PHONE, PIIType.ACCOUNT_NUMBER, PIIType.AMOUNT,
]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


class PIIDetector:
    def __init__(self, include_amounts: bool = False) -> None:
        self.include_amounts = include_amounts

    def detect(self, text: str) -> list[PIIMatch]:
        if not text:
            return []
        found: list[PIIMatch] = []

        def add(t: PIIType, m: re.Match, group: int = 1) -> None:
            found.append(PIIMatch(t, m.start(group), m.end(group), m.group(group)))

        for t, pat in _SECRET_PATTERNS.items():
            for m in pat.finditer(text):
                add(t, m)
        for m in _PASSWORD.finditer(text):
            add(PIIType.PASSWORD, m)
        for m in _CARD.finditer(text):
            d = _digits(m.group(1))
            if 13 <= len(d) <= 19 and luhn_valid(d):
                add(PIIType.CARD_NUMBER, m)
        for m in _AADHAAR.finditer(text):
            if verhoeff_valid(_digits(m.group(1))):
                add(PIIType.AADHAAR, m)
        for pat, t in ((_PAN, PIIType.PAN), (_IFSC, PIIType.IFSC), (_EMAIL, PIIType.EMAIL), (_DOB, PIIType.DOB)):
            for m in pat.finditer(text):
                add(t, m)
        for m in _UPI.finditer(text):
            add(PIIType.UPI_ID, m)
        for pat in (_ADDRESS, _PINCODE_ADDR):
            for m in pat.finditer(text):
                add(PIIType.ADDRESS, m)
        for m in _ACCOUNT_CTX.finditer(text):
            add(PIIType.ACCOUNT_NUMBER, m)
        for m in _PHONE.finditer(text):
            add(PIIType.PHONE, m)
        for m in _ACCOUNT.finditer(text):
            add(PIIType.ACCOUNT_NUMBER, m)
        if self.include_amounts:
            for m in _AMOUNT.finditer(text):
                add(PIIType.AMOUNT, m)
        return _resolve_overlaps(found)

    def contains_secret(self, text: str) -> bool:
        return any(m.type in SECRET_TYPES for m in self.detect(text))


def _resolve_overlaps(matches: list[PIIMatch]) -> list[PIIMatch]:
    rank = {t: i for i, t in enumerate(_PRIORITY)}
    ordered = sorted(matches, key=lambda m: (rank[m.type], -(m.end - m.start)))
    kept: list[PIIMatch] = []
    for m in ordered:
        if all(m.end <= k.start or m.start >= k.end for k in kept):
            kept.append(m)
    return sorted(kept, key=lambda m: m.start)


_BARE_CODE = re.compile(r"^\s*(\d(?:[\s-]?\d){3,7})\s*\.?\s*$")
_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4",
    "chaar": "4", "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
}


def extract_code(text: str) -> str | None:
    """Pull a 4-8 digit code out of a message that is (almost) only that code.

    Handles '123456', '123 456', 'my otp is 123456', and spoken STT output ('one two three four five six').
    """
    m = _BARE_CODE.match(text)
    if m:
        return _digits(m.group(1))
    for pat in _SECRET_PATTERNS.values():
        m = pat.search(text)
        if m:
            return _digits(m.group(1))
    words = re.findall(r"[a-z]+|\d", text.lower())
    if words and all(w in _SPOKEN_DIGITS or w.isdigit() or w in {"otp", "is", "hai", "my", "the", "code"} for w in words):
        digits = "".join(_SPOKEN_DIGITS.get(w, w if w.isdigit() else "") for w in words)
        if 4 <= len(digits) <= 8:
            return digits
    return None
