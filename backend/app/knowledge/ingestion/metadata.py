"""Document metadata model used for retrieval filtering (tenant, product, validity window, access)."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class AccessLevel(StrEnum):
    PUBLIC = "public"  # anyone, incl. unauthenticated visitors
    CUSTOMER = "customer"  # identified/authenticated customers
    INTERNAL = "internal"  # staff/human agents only, never shown to customers


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class DocumentMetadata(BaseModel):
    tenant_id: str
    document_id: str
    version_id: str
    title: str
    doc_type: str = "policy"
    product: str | None = None
    language: str = "en"
    region: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    version: str = "1"
    status: DocumentStatus = DocumentStatus.ACTIVE
    access_level: AccessLevel = AccessLevel.PUBLIC
    tags: list[str] = Field(default_factory=list)
    source: str | None = None


_PRODUCTS = {
    "home_loan": r"home\s*loan|housing\s*loan|mortgage",
    "personal_loan": r"personal\s*loan",
    "credit_card": r"credit\s*card",
    "debit_card": r"debit\s*card",
    "savings_account": r"savings\s*account",
    "fixed_deposit": r"fixed\s*deposit|\bfd\b",
    "insurance": r"insurance|policyholder|premium",
}
_DOC_TYPES = {"faq": r"\bfaq\b|frequently asked", "fees": r"\bfees?\b|schedule of charges", "terms": r"terms|conditions",
              "sop": r"\bsop\b|standard operating", "regulatory": r"\brbi\b|circular|master direction", "policy": r"policy"}


def infer_product(text: str) -> str | None:
    head = text[:3000].lower()
    scores = {p: len(re.findall(rx, head)) for p, rx in _PRODUCTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def infer_doc_type(filename: str, text: str) -> str:
    probe = (filename + " " + text[:600]).lower()
    for t, rx in _DOC_TYPES.items():
        if re.search(rx, probe):
            return t
    return "policy"
