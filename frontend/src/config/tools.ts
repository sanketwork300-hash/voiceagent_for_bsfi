/** Presentation metadata for tools. Purely cosmetic: governance (risk, auth, confirmation) comes from the backend. */
export const TOOL_LABELS: Record<string, { running: string; done: string; service: string }> = {
  search_knowledge: { running: "Searching approved documents", done: "Documents searched", service: "Knowledge base" },
  get_account_balance: { running: "Checking your balance", done: "Balance retrieved", service: "Account service" },
  get_recent_transactions: { running: "Fetching recent transactions", done: "Transactions retrieved", service: "Transaction service" },
  get_loan_details: { running: "Checking your loan", done: "Loan details retrieved", service: "Loan service" },
  get_card_status: { running: "Checking your cards", done: "Card details retrieved", service: "Card service" },
  get_payment_status: { running: "Checking the payment", done: "Payment status retrieved", service: "Payments service" },
  block_card: { running: "Blocking the card", done: "Card service responded", service: "Card service" },
  transfer_money: { running: "Submitting the transfer", done: "Bank responded", service: "Payments service" },
  request_human_handoff: { running: "Connecting you to a specialist", done: "Specialist requested", service: "Contact centre" },
};

export function toolLabel(tool: string, done = false): string {
  const l = TOOL_LABELS[tool];
  if (l) return done ? l.done : l.running;
  const pretty = tool.replace(/_/g, " ");
  return done ? `${pretty} completed` : `Running ${pretty}`;
}

/** Tools whose completion is a financial/account action (rendered as a receipt, never as plain chat text). */
export const ACTION_TOOLS: Record<string, { title: string; confirmLabel: string; successTitle: string; failureTitle: string; consequence: string }> = {
  transfer_money: { title: "Transfer money", confirmLabel: "Confirm transfer", successTitle: "Transfer successful",
                    failureTitle: "Transfer unsuccessful", consequence: "Money will leave your account once you confirm." },
  block_card: { title: "Block card", confirmLabel: "Block card", successTitle: "Card blocked", failureTitle: "Card not blocked",
                consequence: "This immediately stops new transactions on the card and can't be undone." },
};

export const INTENT_STATUS: Record<string, string> = {
  KNOWLEDGE_QUERY: "Looking this up in approved documents",
  CUSTOMER_DATA_QUERY: "Checking your account",
  ACTION_REQUEST: "Preparing your request",
  FRAUD_REQUEST: "Escalating this securely",
  HUMAN_HANDOFF: "Finding a specialist",
  GENERAL_CONVERSATION: "Composing a reply",
};
