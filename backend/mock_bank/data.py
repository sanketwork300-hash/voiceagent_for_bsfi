"""In-memory core-banking fixture for the prototype. Reset with `reset()`."""

from __future__ import annotations

import copy
from datetime import date, timedelta

TODAY = date.today()

_SEED = {
    "customers": {
        "CUST1001": {"customer_id": "CUST1001", "name": "Priya Sharma", "phone": "9876543210", "email": "priya.sharma@example.com"},
        "CUST1002": {"customer_id": "CUST1002", "name": "Arjun Mehta", "phone": "9123456780", "email": "arjun.mehta@example.com"},
    },
    "accounts": {
        "CUST1001": [
            {"account_id": "SB-50100217788", "account_type": "savings", "account_number": "50100217788", "available_balance": 1285230.75,
             "ledger_balance": 1287730.75, "currency": "INR", "branch": "Andheri West, Mumbai"},
        ],
        "CUST1002": [
            {"account_id": "SB-50100994411", "account_type": "savings", "account_number": "50100994411", "available_balance": 15200.00,
             "ledger_balance": 15200.00, "currency": "INR", "branch": "Koramangala, Bengaluru"},
        ],
    },
    "loans": {
        "CUST1001": [
            {"loan_id": "HL-778812345521", "loan_type": "home", "loan_account": "778812345521", "sanctioned_amount": 4000000,
             "outstanding_principal": 2845000.00, "emi_amount": 32500.00, "next_emi_date": (TODAY + timedelta(days=12)).isoformat(),
             "interest_rate": 8.65, "tenure_remaining_months": 148, "status": "ACTIVE"},
        ],
        "CUST1002": [],
    },
    "cards": {
        "CUST1001": [
            {"card_id": "CC-4242", "card_type": "credit", "card_number": "4111111111114242", "network": "VISA", "status": "ACTIVE",
             "credit_limit": 200000, "available_limit": 176550, "outstanding_amount": 23450.00, "minimum_due": 1172.50,
             "payment_due_date": (TODAY + timedelta(days=9)).isoformat()},
            {"card_id": "DC-1881", "card_type": "debit", "card_number": "5500005555551881", "network": "RUPAY", "status": "ACTIVE",
             "linked_account": "50100217788"},
        ],
        "CUST1002": [
            {"card_id": "DC-7713", "card_type": "debit", "card_number": "5500005555557713", "network": "RUPAY", "status": "ACTIVE",
             "linked_account": "50100994411"},
        ],
    },
    "transactions": {
        "CUST1001": [
            {"txn_id": "TXN9001", "date": (TODAY - timedelta(days=1)).isoformat(), "description": "UPI/Swiggy", "amount": 642.00, "type": "debit"},
            {"txn_id": "TXN9002", "date": (TODAY - timedelta(days=2)).isoformat(), "description": "Salary credit - ACME Corp", "amount": 185000.00, "type": "credit"},
            {"txn_id": "TXN9003", "date": (TODAY - timedelta(days=3)).isoformat(), "description": "Home loan EMI", "amount": 32500.00, "type": "debit"},
            {"txn_id": "TXN9004", "date": (TODAY - timedelta(days=4)).isoformat(), "description": "Electricity bill - BEST", "amount": 2310.00, "type": "debit"},
            {"txn_id": "TXN9005", "date": (TODAY - timedelta(days=6)).isoformat(), "description": "ATM withdrawal", "amount": 5000.00, "type": "debit"},
            {"txn_id": "TXN9006", "date": (TODAY - timedelta(days=8)).isoformat(), "description": "UPI/Amazon", "amount": 1899.00, "type": "debit"},
        ],
        "CUST1002": [],
    },
    "payments": {
        "CUST1001": [
            {"payment_ref": "UPI4815162342", "amount": 2310.00, "payee": "BEST Electricity", "status": "SUCCESS",
             "updated_at": (TODAY - timedelta(days=4)).isoformat()},
            {"payment_ref": "NEFT7781234", "amount": 15000.00, "payee": "Rohan Kapoor", "status": "PENDING",
             "updated_at": TODAY.isoformat()},
        ],
        "CUST1002": [],
    },
    "beneficiaries": {
        "CUST1001": [
            {"beneficiary_id": "BEN01", "name": "Rahul Verma", "account_number": "60200110034521", "ifsc": "HDFC0001234"},
            {"beneficiary_id": "BEN02", "name": "Rohan Kapoor", "account_number": "60200110099887", "ifsc": "ICIC0000456"},
        ],
        "CUST1002": [],
    },
}

DB: dict = {}
OTP_CHALLENGES: dict[str, dict] = {}
IDEMPOTENCY: dict[str, dict] = {}
MOCK_OTP = "123456"  # deterministic in the sandbox; a real bank sends it by SMS


def reset() -> None:
    DB.clear()
    DB.update(copy.deepcopy(_SEED))
    OTP_CHALLENGES.clear()
    IDEMPOTENCY.clear()


def mask(number: str) -> str:
    return "XXXX" + number[-4:]


reset()
