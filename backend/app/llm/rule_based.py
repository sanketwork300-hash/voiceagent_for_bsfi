"""Deterministic, offline stand-in for an LLM.

It implements the full LLMProvider contract (tool calling, structured output, streaming) using the
multilingual lexicon and response templates, so the platform, its demos and its test/evaluation
suites run end-to-end with no model endpoint or API key. It goes through exactly the same runtime,
tool gateway and policy engine as a real model — it gets no special privileges.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.i18n import lexicon as lx
from app.i18n.formatting import format_date, format_inr
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, LLMToolCall, LLMUsage

_LANG_TAG = re.compile(r"RESPONSE_LANGUAGE:\s*([a-z]{2}(?:-[A-Za-z]{4})?)")

T: dict[str, dict[str, str]] = {
    "balance": {
        "en": "Your {account_type} account {masked} has an available balance of {amount}.",
        "hi-Latn": "Aapke {account_type} account {masked} mein available balance {amount} hai.",
        "hi": "आपके {account_type} खाते {masked} में उपलब्ध बैलेंस {amount} है।",
    },
    "loan": {
        "en": "Your {loan_type} loan ({masked}) has an outstanding balance of {outstanding}. Your next EMI of {emi} is due on {due}. Interest rate: {rate}% p.a., {tenure} months remaining.",
        "hi-Latn": "Aapke {loan_type} loan ({masked}) ka outstanding balance {outstanding} hai. Agli EMI {emi} ki hai, jo {due} ko due hai. Interest rate {rate}% p.a. hai, aur {tenure} mahine baaki hain.",
        "hi": "आपके {loan_type} लोन ({masked}) का बकाया {outstanding} है। अगली EMI {emi} की है, जो {due} को देय है।",
    },
    "card_credit": {
        "en": "Your credit card {masked} is {status}. Total outstanding is {outstanding}, minimum due is {min_due}, and the payment due date is {due}. Available limit: {available}.",
        "hi-Latn": "Aapke credit card {masked} ka total outstanding {outstanding} hai. Minimum due {min_due} hai, aur due date {due} hai. Available limit {available} hai.",
        "hi": "आपके क्रेडिट कार्ड {masked} का कुल बकाया {outstanding} है। न्यूनतम देय {min_due} है और अंतिम तिथि {due} है।",
    },
    "card_debit": {
        "en": "Your debit card {masked} is {status}.",
        "hi-Latn": "Aapka debit card {masked} {status} hai.",
        "hi": "आपका डेबिट कार्ड {masked} {status} है।",
    },
    "block_card": {
        "en": "Done. Your {card_type} card {masked} has been blocked. Reference number: {reference}. A replacement card can be requested from the app or branch.",
        "hi-Latn": "Ho gaya. Aapka {card_type} card {masked} block kar diya gaya hai. Reference number: {reference}.",
        "hi": "आपका {card_type} कार्ड {masked} ब्लॉक कर दिया गया है। संदर्भ संख्या: {reference}।",
    },
    "which_card": {
        "en": "You have more than one card: {cards}. Which one should I block?",
        "hi-Latn": "Aapke paas ek se zyada card hain: {cards}. Kaunsa card block karna hai?",
        "hi": "आपके पास एक से अधिक कार्ड हैं: {cards}। कौन सा कार्ड ब्लॉक करना है?",
    },
    "payment": {
        "en": "Payment {ref} of {amount} to {payee} is {status} (as of {when}).",
        "hi-Latn": "Payment {ref} ({amount}, {payee} ko) ka status {status} hai ({when}).",
        "hi": "भुगतान {ref} ({amount}, {payee}) की स्थिति: {status}।",
    },
    "transfer": {
        "en": "Transfer successful. {amount} has been sent to {payee} ({account}). Transaction reference: {reference}.",
        "hi-Latn": "Transfer ho gaya. {amount} {payee} ({account}) ko bhej diye gaye hain. Transaction reference: {reference}.",
        "hi": "ट्रांसफर सफल रहा। {amount} {payee} ({account}) को भेज दिए गए हैं। संदर्भ: {reference}।",
    },
    "txn_header": {"en": "Here are your recent transactions:", "hi-Latn": "Aapke recent transactions:", "hi": "आपके हाल के लेन-देन:"},
    "kb": {
        "en": "{answer} [1]",
        "hi-Latn": "{title} ke anusaar: {answer} [1]",
        "hi": "{title} के अनुसार: {answer} [1]",
    },
    "kb_none": {
        "en": "I couldn't find that in our approved documents. I can connect you to an agent if you'd like.",
        "hi-Latn": "Mujhe yeh jaankari hamare approved documents mein nahi mili. Aap chahein to main aapko agent se connect kar sakta hoon.",
        "hi": "मुझे यह जानकारी स्वीकृत दस्तावेज़ों में नहीं मिली।",
    },
    "error": {
        "en": "Sorry, I couldn't complete that right now: {error}",
        "hi-Latn": "Maaf kijiye, abhi yeh nahi ho paya: {error}",
        "hi": "क्षमा करें, अभी यह नहीं हो पाया: {error}",
    },
    "denied": {
        "en": "I'm not able to do that here: {error}",
        "hi-Latn": "Main yeh yahan nahi kar sakta: {error}",
        "hi": "मैं यह यहाँ नहीं कर सकता: {error}",
    },
    "greeting": {
        "en": "Hello! I can help with balances, transactions, loans, cards, payments, transfers and product questions. How can I help?",
        "hi-Latn": "Namaste! Main balance, transactions, loan, card, payments, transfer aur product ki jaankari mein madad kar sakta hoon. Bataiye, kya madad karun?",
        "hi": "नमस्ते! मैं बैलेंस, लेन-देन, लोन, कार्ड, भुगतान और उत्पाद जानकारी में मदद कर सकता हूँ।",
    },
    "thanks": {"en": "You're welcome! Anything else?", "hi-Latn": "Aapka swagat hai! Aur kuch?", "hi": "आपका स्वागत है! और कुछ?"},
    "fallback": {
        "en": "I can help with account balances, transactions, loans, cards, payments, transfers and product information. Could you tell me a bit more?",
        "hi-Latn": "Main balance, transactions, loan, card, payment aur transfer mein madad kar sakta hoon. Thoda aur bataiye?",
        "hi": "कृपया थोड़ा और बताइए, मैं कैसे मदद करूँ?",
    },
    "handoff": {
        "en": "I'm connecting you to a human agent now.",
        "hi-Latn": "Main aapko abhi ek human agent se connect kar raha hoon.",
        "hi": "मैं आपको अभी एक एजेंट से जोड़ रहा हूँ।",
    },
}


def t(key: str, lang: str, **kw: Any) -> str:
    table = T[key]
    return table.get(lang, table["en"]).format(**kw)


def _lang(messages: list[LLMMessage]) -> str:
    for m in messages:
        if m.role == "system" and m.content and (g := _LANG_TAG.search(m.content)):
            tag = g.group(1)
            return tag if tag in ("en", "hi", "hi-Latn") else "en"
    return "en"


def _user_texts(messages: list[LLMMessage]) -> list[str]:
    return [m.content or "" for m in messages if m.role == "user"]


def _call(name: str, args: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        tool_calls=[LLMToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=name, arguments=args)],
        finish_reason="tool_calls", provider="rule_based", model="rule-based-v1",
    )


def _text(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop", provider="rule_based", model="rule-based-v1",
                       usage=LLMUsage(prompt_tokens=0, completion_tokens=len(content.split())))


class RuleBasedLLMProvider(LLMProvider):
    name = "rule_based"
    model = "rule-based-v1"

    async def complete(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None, json_schema=None) -> LLMResponse:
        if json_schema:
            return _text(json.dumps(self._structured(messages, json_schema["name"])))
        system = next((m.content or "" for m in messages if m.role == "system"), "")
        if "TASK: SUMMARIZE" in system:
            return _text(self._summarize(messages))
        lang = _lang(messages)
        last = messages[-1]
        if last.role == "tool":
            return _text(self._compose_from_tools(messages, lang))
        return self._route(messages, {t.name for t in tools or []}, lang)

    # ---- structured output ----------------------------------------------------------------------
    def _structured(self, messages: list[LLMMessage], schema_name: str) -> dict[str, Any]:
        text = (_user_texts(messages) or [""])[-1]
        if "Latest message:" in text:
            text = text.split("Latest message:", 1)[1].strip()
        if schema_name == "IntentClassification":
            intent, conf, entities = lx.rule_classify(text)
            return {"intent": intent, "confidence": conf, "entities": entities, "reasoning": "lexicon rules"}
        return {}

    def _summarize(self, messages: list[LLMMessage]) -> str:
        users = [u for u in _user_texts(messages) if u.strip()]
        convo = next((m.content for m in messages if m.role == "user"), "") or ""
        lines = [ln for ln in convo.splitlines() if ln.startswith("customer:")]
        asks = [ln.removeprefix("customer:").strip() for ln in lines][-3:] or users[-3:]
        return "Customer asked: " + " | ".join(asks) if asks else "No customer messages."

    # ---- tool routing ---------------------------------------------------------------------------
    def _route(self, messages: list[LLMMessage], tools: set[str], lang: str) -> LLMResponse:
        users = _user_texts(messages)
        text = users[-1] if users else ""
        prev = users[-2] if len(users) > 1 else ""

        def offer(name: str, args: dict[str, Any]) -> LLMResponse | None:
            return _call(name, args) if name in tools else None

        intent, _, ent = lx.rule_classify(text)
        if intent == "HUMAN_HANDOFF" and (r := offer("request_human_handoff", {"reason": "CUSTOMER_REQUEST", "note": text[:200]})):
            return r
        if intent == "FRAUD_REQUEST":
            if lx.ACTION_BLOCK.search(text) and (r := offer("block_card", {k: v for k, v in {"card_type": ent.get("card_type"), "reason": "fraud"}.items() if v})):
                return r
            if r := offer("request_human_handoff", {"reason": "FRAUD", "note": text[:200]}):
                return r
        # follow-up to "which card should I block?"
        if lx.card_type(text) and not lx.ACTION_BLOCK.search(text) and lx.ACTION_BLOCK.search(prev) and len(text.split()) <= 6:
            if r := offer("block_card", {"card_type": lx.card_type(text), "reason": "customer_request"}):
                return r
        if lx.ACTION_TRANSFER.search(text) and "transfer_money" in tools:
            args = {"amount": ent.get("amount"), "payee_name": ent.get("payee_name"), "currency": "INR"}
            if args["amount"] is None or not args["payee_name"]:
                return _text("Sure. How much would you like to transfer, and to which registered beneficiary?")
            return _call("transfer_money", args)
        if lx.ACTION_BLOCK.search(text) and lx.CARD.search(text):
            if ct := lx.card_type(text):
                if r := offer("block_card", {"card_type": ct, "reason": "customer_request"}):
                    return r
            if r := offer("get_card_status", {}):
                return r
        if intent == "KNOWLEDGE_QUERY" and (r := offer("search_knowledge", {"query": text})):
            return r
        if lx.PAYMENT_STATUS.search(text):
            args = {"payment_ref": ref} if (ref := lx.extract_payment_ref(text)) else {}
            if r := offer("get_payment_status", args):
                return r
        if intent in ("CUSTOMER_DATA_QUERY", "ACTION_REQUEST"):
            if lx.CARD.search(text) or lx.OUTSTANDING.search(text):
                args = {"card_type": ct} if (ct := lx.card_type(text)) else {}
                if r := offer("get_card_status", args):
                    return r
            if lx.LOAN.search(text) and (r := offer("get_loan_details", {})):
                return r
            if lx.TRANSACTIONS.search(text) and (r := offer("get_recent_transactions", {"limit": 5})):
                return r
            if lx.BALANCE.search(text) and (r := offer("get_account_balance", {})):
                return r
        if lx.GREETING.search(text):
            return _text(t("greeting", lang))
        if lx.THANKS.search(text):
            return _text(t("thanks", lang))
        if intent == "KNOWLEDGE_QUERY" or "?" in text:
            if r := offer("search_knowledge", {"query": text}):
                return r
        return _text(t("fallback", lang))

    # ---- grounded response composition ----------------------------------------------------------
    def _compose_from_tools(self, messages: list[LLMMessage], lang: str) -> str:
        results: list[tuple[str, dict[str, Any]]] = []
        for m in reversed(messages):
            if m.role != "tool":
                break
            try:
                results.append((m.name or "", json.loads(m.content or "{}")))
            except json.JSONDecodeError:
                results.append((m.name or "", {"ok": False, "error": "unreadable tool output"}))
        results.reverse()
        query = (_user_texts(messages) or [""])[-1]
        users = _user_texts(messages)
        return " ".join(self._render(name, res, lang, query, users) for name, res in results).strip()

    def _render(self, name: str, res: dict[str, Any], lang: str, query: str, users: list[str]) -> str:
        if not res.get("ok"):
            key = "denied" if res.get("policy_decision") == "DENY" else "error"
            return t(key, lang, error=res.get("error", "unknown error"))
        d = res.get("data") or {}
        match name:
            case "get_account_balance":
                return " ".join(
                    t("balance", lang, account_type=a.get("account_type", ""), masked=a.get("account_number_masked", ""),
                      amount=format_inr(a.get("available_balance")))
                    for a in d.get("accounts", [])
                ) or t("error", lang, error="no accounts found")
            case "get_loan_details":
                return " ".join(
                    t("loan", lang, loan_type=ln.get("loan_type", ""), masked=ln.get("loan_account_masked", ""),
                      outstanding=format_inr(ln.get("outstanding_principal")), emi=format_inr(ln.get("emi_amount")),
                      due=format_date(ln.get("next_emi_date")), rate=ln.get("interest_rate"), tenure=ln.get("tenure_remaining_months"))
                    for ln in d.get("loans", [])
                ) or t("error", lang, error="no loans found")
            case "get_card_status":
                cards = d.get("cards", [])
                wants_block = any(lx.ACTION_BLOCK.search(u) for u in users[-2:])
                if wants_block and len(cards) > 1:
                    listing = ", ".join(f"{c['card_type']} card {c['card_number_masked']}" for c in cards)
                    return t("which_card", lang, cards=listing)
                out = []
                for c in cards:
                    if c.get("card_type") == "credit":
                        out.append(t("card_credit", lang, masked=c.get("card_number_masked"), status=c.get("status", "").lower(),
                                     outstanding=format_inr(c.get("outstanding_amount")), min_due=format_inr(c.get("minimum_due")),
                                     due=format_date(c.get("payment_due_date")), available=format_inr(c.get("available_limit"))))
                    else:
                        out.append(t("card_debit", lang, masked=c.get("card_number_masked"), status=c.get("status", "").lower()))
                return " ".join(out) or t("error", lang, error="no cards found")
            case "block_card":
                return t("block_card", lang, card_type=d.get("card_type", ""), masked=d.get("card_number_masked", ""),
                         reference=d.get("reference", ""))
            case "get_recent_transactions":
                rows = [f"{format_date(x.get('date'))}: {x.get('description')} — {'-' if x.get('type') == 'debit' else '+'}{format_inr(x.get('amount'))}"
                        for x in d.get("transactions", [])]
                return t("txn_header", lang) + " " + "; ".join(rows) + "."
            case "get_payment_status":
                return t("payment", lang, ref=d.get("payment_ref"), amount=format_inr(d.get("amount")), payee=d.get("payee"),
                         status=d.get("status"), when=format_date(d.get("updated_at")))
            case "transfer_money":
                return t("transfer", lang, amount=format_inr(d.get("amount")), payee=d.get("payee_name"),
                         account=d.get("payee_account_masked"), reference=d.get("transaction_ref"))
            case "search_knowledge":
                hits = d.get("results", [])
                if not hits:
                    return t("kb_none", lang)
                top = hits[0]
                return t("kb", lang, title=top.get("title", ""), answer=_best_sentences(top.get("snippet", ""), query))
            case "request_human_handoff":
                return t("handoff", lang)
            case _:
                return json.dumps(d)[:300]


_STOP = {"what", "is", "are", "the", "a", "an", "of", "for", "my", "to", "in", "on", "and", "do", "i", "how", "much", "kya", "hai"}
_NUMERIC_Q = re.compile(r"\b(rate|rates|charge|charges|fee|fees|cost|how much|kitna|percent|limit|minimum|penalty)\b", re.I)


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _best_sentences(snippet: str, query: str, n: int = 2) -> str:
    first, _, rest = snippet.partition("\n")
    if rest and len(first) < 90 and not first.rstrip().endswith((".", "?", "!")):
        snippet = rest  # drop the section heading that chunks carry for retrieval context
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", snippet) if len(s.strip()) > 20]
    if not sentences:
        return snippet.strip()
    q = {_stem(w) for w in re.findall(r"[a-z]+", query.lower()) if w not in _STOP}
    numeric_bonus = 1.5 if _NUMERIC_Q.search(query) else 0.3

    def score(s: str) -> float:
        words = {_stem(w) for w in re.findall(r"[a-z]+", s.lower())}
        return sum(1.0 for w in q if w in words) + (numeric_bonus if re.search(r"\d", s) else 0)

    ranked = sorted(range(len(sentences)), key=lambda i: -score(sentences[i]))[:n]
    return " ".join(sentences[i] for i in sorted(ranked))
