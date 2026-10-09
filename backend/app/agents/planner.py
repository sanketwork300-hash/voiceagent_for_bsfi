"""Turn planning: intent classification (LLM structured output, lexicon fallback) and routing hints."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.agents.state import SessionState
from app.domain import Intent
from app.i18n import lexicon
from app.llm.base import LLMError, LLMMessage, LLMProvider

log = logging.getLogger(__name__)

CLASSIFIER_PROMPT = """Classify the customer's latest message for an Indian bank's assistant. Messages may be in English,
Hindi, Hinglish or other Indian languages, possibly code-mixed.
Intents:
- KNOWLEDGE_QUERY: general product/policy/fee/rate/process questions (not about the customer's own account)
- CUSTOMER_DATA_QUERY: questions about the customer's own balance, transactions, loans, cards, payments, claims
- ACTION_REQUEST: the customer wants something done (block card, transfer money, raise a request)
- FRAUD_REQUEST: reports of fraud, theft, unauthorised transactions, scams, compromised credentials
- HUMAN_HANDOFF: explicitly asks for a human/agent/representative
- GENERAL_CONVERSATION: greetings, thanks, chit-chat, unclear
Return JSON only. Extract entities when present (amount as a number in INR, payee_name, card_type credit|debit, product)."""


class IntentClassification(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    entities: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


@dataclass
class TurnPlan:
    intent: Intent
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    carried_over: bool = False
    source: str = "llm"


class IntentClassifier:
    def __init__(self, llm: LLMProvider, min_confidence: float = 0.55, use_llm: bool = True) -> None:
        self.llm = llm
        self.min_confidence = min_confidence
        self.use_llm = use_llm  # False: keyword rules only (no model round trip before the main turn)

    async def classify(self, text: str, recent: list[str]) -> IntentClassification:
        context = "\n".join(f"- {r}" for r in recent[-3:]) or "(none)"
        if not self.use_llm:
            intent, conf, ents = lexicon.rule_classify(text)
            return IntentClassification(intent=Intent(intent), confidence=conf, entities=ents, reasoning="lexicon fallback")
        try:
            out = await self.llm.structured([
                LLMMessage(role="system", content=CLASSIFIER_PROMPT),
                LLMMessage(role="user", content=f"Previous customer messages:\n{context}\n\nLatest message:\n{text}"),
            ], IntentClassification)
            if out.confidence >= self.min_confidence:
                return out
        except LLMError:
            log.warning("intent classification via LLM failed; using lexicon")
        intent, conf, ents = lexicon.rule_classify(text)
        return IntentClassification(intent=Intent(intent), confidence=conf, entities=ents, reasoning="lexicon fallback")


class Planner:
    def __init__(self, classifier: IntentClassifier) -> None:
        self.classifier = classifier

    async def plan(self, text: str, state: SessionState, recent_user_messages: list[str]) -> TurnPlan:
        c = await self.classifier.classify(text, recent_user_messages)
        plan = TurnPlan(intent=c.intent, confidence=c.confidence, entities=c.entities,
                        source="lexicon" if c.reasoning == "lexicon fallback" else "llm")
        # Short follow-ups ("credit card", "the second one", "haan wahi") continue the previous task.
        if (c.intent == Intent.GENERAL_CONVERSATION and state.current_intent in
                (Intent.ACTION_REQUEST, Intent.CUSTOMER_DATA_QUERY, Intent.FRAUD_REQUEST, Intent.KNOWLEDGE_QUERY)
                and len(text.split()) <= 6 and not lexicon.GREETING.search(text) and not lexicon.THANKS.search(text)):
            plan.intent, plan.carried_over = state.current_intent, True
        return plan
