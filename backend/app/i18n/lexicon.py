"""Multilingual BFSI lexicon: intent cues and entity extraction (English, Hinglish, Devanagari Hindi, ...).

Used (a) as the deterministic fallback when an LLM's structured output is unavailable, and (b) by the
offline rule-based LLM. Production intent classification is done by the LLM with structured output.
"""

from __future__ import annotations

import re
from typing import Any

_I = re.I


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{a})" for a in alts), _I)


HUMAN = _rx(
    r"\b(talk|speak|connect|transfer)\b.{0,25}\b(human|person|agent|executive|representative|officer|someone|manager)\b",
    r"\b(human|real person|customer care|call centre|call center)\b", r"\binsaan\b", r"\b(agent|executive) se baat\b",
    r"\bbaat karni hai\b", r"इंसान|एजेंट से बात|ग्राहक सेवा",
)
FRAUD = _rx(
    r"\bfraud", r"\bstole|\bstolen\b|\btheft\b|\bchori\b", r"\bunauthori[sz]ed\b", r"\bscam", r"\bhack(ed)?\b",
    r"\b(didn'?t|did not|never) (make|do|authori[sz]e)\b", r"\bsomeone (has )?(stole|used|took|withdrew)\b",
    r"\bpaise? (kat|nikal) (gaye|liye)\b", r"\bdhokha\b", r"\bphishing\b", r"धोखा|चोरी|फ्रॉड|पैसे कट गए",
)
ACTION_BLOCK = _rx(r"\bblock\b", r"\bfreeze\b", r"\bband (kar|karo|kardo|kar do)\b", r"\bdeactivate\b", r"ब्लॉक|बंद कर")
ACTION_TRANSFER = _rx(
    r"\btransfer\b", r"\bsend\b.{0,20}(money|\brs\b|₹|rupees|inr|\d)", r"\bbhej(o|na|do| do| dijiye)\b", r"\bpay\b.{0,15}\bto\b",
    r"भेज|ट्रांसफर",
)
ACTION_SERVICE = _rx(r"\b(raise|create|open|log)\b.{0,15}\b(request|ticket|complaint)\b")
POSSESSIVE = _rx(
    r"\b(my|mine|me|i|i'm|i've)\b", r"\b(mera|meri|mere|mujhe|mujhko|apna|apni|hamara|hamari|maza|majha|majhi|enakku|naaku)\b",
    r"मेरा|मेरी|मेरे|मुझे|माझ",
)
DATA_NOUNS = _rx(
    r"\bbalance\b", r"\btransactions?\b", r"\bstatement\b", r"\bloan\b", r"\bemi\b", r"\bcard\b", r"\boutstanding\b",
    r"\bdues?\b", r"\bpayment\b", r"\baccount\b", r"\bkhata\b", r"\bpaisa|paise\b", r"\bclaim\b", r"\blen ?den\b",
    r"बैलेंस|खाता|लोन|कार्ड|लेन-?देन",
)
KNOWLEDGE_CUES = _rx(
    r"\binterest rates?\b", r"\b(charges?|fees?|penalt(y|ies))\b", r"\bpolic(y|ies)\b", r"\beligib", r"\bdocuments? required\b",
    r"\bforeclos", r"\bprepay", r"\bhow (do|can|to)\b", r"\bwhat (is|are) (the|a)\b", r"\bterms\b", r"\bfaq\b",
    r"\bminimum balance\b", r"\bkyc\b", r"\bprocess\b", r"\blimit\b.{0,10}\bfor\b", r"\bkya hai\b.{0,10}\b(rate|charge|fee)",
    r"ब्याज दर|शुल्क|नियम",
)
PAYMENT_STATUS = _rx(r"\bpayment\b.{0,20}\bstatus\b", r"\bstatus\b.{0,20}\b(payment|transaction|txn|upi)\b", r"\bpayment ka status\b")
TRANSACTIONS = _rx(r"\btransactions?\b", r"\bstatement\b", r"\blen ?den\b", r"\bspent\b", r"\bdebits?\b", r"लेन-?देन")
LOAN = _rx(r"\bloan\b", r"\bemi\b", r"\bkarz\b", r"लोन|कर्ज")
CARD = _rx(r"\bcard\b", r"कार्ड")
CREDIT = _rx(r"\bcredit\b", r"क्रेडिट")
DEBIT = _rx(r"\bdebit\b", r"\batm card\b", r"डेबिट")
OUTSTANDING = _rx(r"\boutstanding\b", r"\bdues?\b", r"\bbill\b", r"\bkitna (baaki|bakaya)\b")
BALANCE = _rx(r"\bbalance\b", r"\bkitna paisa\b", r"\bpaise kitne\b", r"बैलेंस|शेष")
GREETING = _rx(r"^\s*(hi|hello|hey|namaste|namaskar|good (morning|afternoon|evening)|vanakkam)\b", r"^\s*नमस्ते")
THANKS = _rx(r"\b(thanks|thank you|thx|dhanyavaad|shukriya)\b", r"धन्यवाद|शुक्रिया")
AFFIRM = _rx(
    r"^\s*(yes|y|yeah|yep|sure|ok|okay|confirm(ed)?|go ahead|proceed|do it|haan?|ha ji|haan ji|ji|ji haan|theek hai|thik hai|kar do|karo)\b",
    r"^\s*(हाँ|हां|जी|ठीक है)",
)
NEGATE = _rx(r"^\s*(no|nope|nah|cancel|stop|don'?t|do not|nahi|nahin|mat karo|ruk(o|iye)|rehne do)\b", r"^\s*(नहीं|ना|रद्द)")
# "Wait!" / "stop!" barged in after an action may already have been sent
STOP = _rx(r"^\s*(wait|hold on|hang on|stop|cancel|no+|don'?t|ruk(o|iye| jao)?|ek (minute|min)|mat karo|band karo|rehne do)\b",
           r"^\s*(रुको|रुकिए|रुक जाओ|नहीं|मत करो)")

# Words that turn a "yes" into a modification ("yes, but make it ₹50,000", "haan lekin Rohan ko").
AMEND = _rx(
    r"\b(but|instead|change|make it|actually|only|except|however|rather|different|another|other|modify|update|correct|wrong|more|less)\b",
    r"\b(lekin|magar|balki|badal|badlo|nahi balki)\b", r"लेकिन|मगर|बदल",
)

_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr|rupees?)\s?(\d[\d,]*(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|cr|k|thousand|hazaar)?"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|cr|k|thousand|hazaar)?\s*(?:₹|rs\.?|inr|rupees?|rupaye|rupay)"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|cr|k|thousand|hazaar)\b",
    _I,
)
_MULT = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "crore": 1e7, "cr": 1e7, "k": 1e3, "thousand": 1e3, "hazaar": 1e3}
_PAYEE = re.compile(r"\bto\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)|\b([A-Z][a-zA-Z]+)\s+ko\b")
_PAYMENT_REF = re.compile(r"\b((?:UPI|TXN|PAY|NEFT|IMPS)[A-Z0-9]{6,})\b", _I)


def extract_amount(text: str) -> float | None:
    m = _AMOUNT.search(text)
    if not m:
        return None
    num, unit = next(((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i)), (None, None))
    if num is None:
        return None
    value = float(num.replace(",", ""))
    return value * _MULT.get((unit or "").lower(), 1)


def extract_payee(text: str) -> str | None:
    m = _PAYEE.search(text)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or "").strip()
    return name or None


def extract_payment_ref(text: str) -> str | None:
    m = _PAYMENT_REF.search(text)
    return m.group(1).upper() if m else None


def card_type(text: str) -> str | None:
    if CREDIT.search(text):
        return "credit"
    if DEBIT.search(text):
        return "debit"
    return None


def rule_classify(text: str) -> tuple[str, float, dict[str, Any]]:
    """Returns (intent, confidence, entities)."""
    t = text or ""
    entities: dict[str, Any] = {}
    if (a := extract_amount(t)) is not None:
        entities["amount"] = a
    if p := extract_payee(t):
        entities["payee_name"] = p
    if c := card_type(t):
        entities["card_type"] = c
    if HUMAN.search(t):
        return "HUMAN_HANDOFF", 0.9, entities
    if FRAUD.search(t):
        return "FRAUD_REQUEST", 0.9, entities
    if ACTION_BLOCK.search(t) or ACTION_TRANSFER.search(t) or ACTION_SERVICE.search(t):
        return "ACTION_REQUEST", 0.85, entities
    if PAYMENT_STATUS.search(t) or (POSSESSIVE.search(t) and DATA_NOUNS.search(t)):
        return "CUSTOMER_DATA_QUERY", 0.85, entities
    if KNOWLEDGE_CUES.search(t):
        return "KNOWLEDGE_QUERY", 0.8, entities
    if DATA_NOUNS.search(t) and re.search(r"\?|\bkitna|\bwhat|\bhow much|\bshow|\btell", t, _I):
        return "KNOWLEDGE_QUERY", 0.55, entities
    return "GENERAL_CONVERSATION", 0.6, entities


def confirmation_reply(text: str) -> str:
    """Classify a reply to "please confirm X": affirm | negate | amend | unclear | other.

    Only a short, unconditional yes is `affirm`. Any amount, number, payee or modifier makes it `amend`: the frozen
    action is dropped and the message is processed as a new request (which needs its own confirmation).
    """
    t = (text or "").strip()
    amended = bool(re.search(r"\d", t)) or extract_amount(t) is not None or bool(AMEND.search(t)) or extract_payee(t) is not None
    if amended:
        return "amend"
    if NEGATE.search(t):
        return "negate"
    if AFFIRM.search(t):
        return "affirm" if len(t.split()) <= 6 else "unclear"
    return "other"
