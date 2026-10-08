/**
 * MOCK — the backend deliberately stores no customer records (the institution is the system of record) and does not
 * yet expose a customer-360 API. This sample data mirrors the backend's mock bank (backend/mock_bank/data.py) so the
 * numbers agree with what the agent returns in chat. Replace with a real `/customers` integration when available.
 */
import type { AuthState } from "@/types/domain";

export const CUSTOMERS_SOURCE = "mock" as const;

export interface CustomerRecord {
  id: string;
  name: string;
  phoneMasked: string;
  emailMasked: string;
  customerSince: string;
  segment: string;
  kycStatus: "Verified" | "Re-KYC due" | "Pending";
  riskSignals: { label: string; level: "low" | "medium" | "high"; at: string }[];
  accounts: { type: string; numberMasked: string; available: number; ledger: number; branch: string }[];
  cards: { type: "credit" | "debit"; network: string; numberMasked: string; status: "ACTIVE" | "BLOCKED"; outstanding?: number; limit?: number; dueDate?: string }[];
  loans: { type: string; numberMasked: string; outstanding: number; emi: number; nextEmi: string; rate: number; tenureLeft: number }[];
  transactions: { id: string; date: string; description: string; amount: number; type: "debit" | "credit"; channel: string; status: string }[];
  cases: { id: string; title: string; status: "Open" | "Resolved"; opened: string }[];
  serviceRequests: { id: string; title: string; status: string; opened: string }[];
  lastAuth: { state: AuthState; method: string; at: string };
}

const today = new Date();
const d = (offset: number) => new Date(today.getTime() + offset * 86400000).toISOString().slice(0, 10);

export const MOCK_CUSTOMERS: CustomerRecord[] = [
  {
    id: "CUST1001", name: "Priya Sharma", phoneMasked: "+91 ••••••3210", emailMasked: "p•••@example.com", customerSince: "2019",
    segment: "Retail · Salaried", kycStatus: "Verified",
    riskSignals: [{ label: "New device login", level: "low", at: d(-3) }],
    accounts: [{ type: "Savings", numberMasked: "•••• 7788", available: 1285230.75, ledger: 1287730.75, branch: "Andheri West, Mumbai" }],
    cards: [
      { type: "credit", network: "Visa", numberMasked: "•••• 4242", status: "ACTIVE", outstanding: 23450, limit: 200000, dueDate: d(9) },
      { type: "debit", network: "RuPay", numberMasked: "•••• 1881", status: "ACTIVE" },
    ],
    loans: [{ type: "Home loan", numberMasked: "•••• 5521", outstanding: 2845000, emi: 32500, nextEmi: d(12), rate: 8.65, tenureLeft: 148 }],
    transactions: [
      { id: "TXN9001", date: d(-1), description: "UPI · Swiggy", amount: 642, type: "debit", channel: "UPI", status: "Completed" },
      { id: "TXN9002", date: d(-2), description: "Salary credit · ACME Corp", amount: 185000, type: "credit", channel: "NEFT", status: "Completed" },
      { id: "TXN9003", date: d(-3), description: "Home loan EMI", amount: 32500, type: "debit", channel: "NACH", status: "Completed" },
      { id: "TXN9004", date: d(-4), description: "Electricity bill · BEST", amount: 2310, type: "debit", channel: "BBPS", status: "Completed" },
      { id: "TXN9005", date: d(-6), description: "ATM withdrawal", amount: 5000, type: "debit", channel: "ATM", status: "Completed" },
    ],
    cases: [{ id: "CASE-2041", title: "Duplicate card charge", status: "Resolved", opened: d(-40) }],
    serviceRequests: [{ id: "SR-77812", title: "Cheque book request", status: "Dispatched", opened: d(-8) }],
    lastAuth: { state: "FULLY_AUTHENTICATED", method: "App login (bank IdP)", at: d(0) },
  },
  {
    id: "CUST1002", name: "Arjun Mehta", phoneMasked: "+91 ••••••6780", emailMasked: "a•••@example.com", customerSince: "2022",
    segment: "Retail · Self-employed", kycStatus: "Re-KYC due",
    riskSignals: [],
    accounts: [{ type: "Savings", numberMasked: "•••• 4411", available: 15200, ledger: 15200, branch: "Koramangala, Bengaluru" }],
    cards: [{ type: "debit", network: "RuPay", numberMasked: "•••• 7713", status: "ACTIVE" }],
    loans: [],
    transactions: [],
    cases: [],
    serviceRequests: [],
    lastAuth: { state: "IDENTIFIED", method: "Caller ID", at: d(-12) },
  },
];

export async function listCustomers(query = ""): Promise<CustomerRecord[]> {
  const q = query.trim().toLowerCase();
  return MOCK_CUSTOMERS.filter((c) => !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q));
}

export async function getCustomer(id: string): Promise<CustomerRecord | null> {
  return MOCK_CUSTOMERS.find((c) => c.id === id) ?? null;
}
