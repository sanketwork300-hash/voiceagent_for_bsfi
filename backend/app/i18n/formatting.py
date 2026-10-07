"""Indian-locale number/currency formatting for text, and speech-friendly rendering for TTS."""

from __future__ import annotations

import re
from datetime import date, datetime


def group_indian(n: int) -> str:
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if n < 0 else "") + s


def format_inr(amount: float | int | str | None) -> str:
    if amount is None or amount == "":
        return "-"
    value = float(amount)
    whole = int(abs(value))
    paise = round((abs(value) - whole) * 100)
    if paise == 100:
        whole, paise = whole + 1, 0
    out = "₹" + group_indian(whole)
    if paise:
        out += f".{paise:02d}"
    return ("-" if value < 0 else "") + out


def format_date(value: str | date | datetime | None) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value.strftime("%d %b %Y")


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _words_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def number_to_indian_words(n: int) -> str:
    if n == 0:
        return "zero"
    parts = []
    for div, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= div:
            parts.append(f"{number_to_indian_words(n // div) if div == 10_000_000 else _words_below_1000(n // div)} {name}")
            n %= div
    if n:
        parts.append(_words_below_1000(n))
    return " ".join(parts)


def _hindi_amount(n: int) -> str:
    # Hinglish speech keeps lakh/crore units but uses digits per unit, which Indic TTS voices read naturally.
    parts = []
    for div, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "hazaar")):
        if n >= div:
            parts.append(f"{n // div} {name}")
            n %= div
    if n:
        parts.append(str(n))
    return " ".join(parts) or "0"


_AMOUNT_RE = re.compile(r"₹\s?(-?[\d,]+)(?:\.(\d{1,2}))?")
_MASKED_RE = re.compile(r"\b(?:X{2,}|x{2,}|\*{2,})[-\s]?(?:X{2,}[-\s]?)*(\d{4})\b")
_CITATION_RE = re.compile(r"\s?\[\d+(?:,\s?\d+)*\]")
_MD_RE = re.compile(r"(\*\*|__|`|#+\s|^\s*[-*]\s)", re.M)


def to_speech_text(text: str, language: str = "en") -> str:
    """Render agent text for TTS: amounts in lakh/crore words, masked numbers as 'ending in', no markdown/citations."""

    def amount(m: re.Match) -> str:
        whole = int(m.group(1).replace(",", ""))
        paise = int((m.group(2) or "0").ljust(2, "0"))
        if language == "hi":
            spoken = f"{_hindi_amount(whole)} rupaye"
            return spoken + (f" {paise} paise" if paise else "")
        spoken = f"{number_to_indian_words(whole)} rupees"
        return spoken + (f" and {number_to_indian_words(paise)} paise" if paise else "")

    out = _CITATION_RE.sub("", text)
    out = _MD_RE.sub("", out)
    out = _AMOUNT_RE.sub(amount, out)
    out = _MASKED_RE.sub(lambda m: f"ending in {' '.join(m.group(1))}", out)
    return re.sub(r"\s{2,}", " ", out).strip()
