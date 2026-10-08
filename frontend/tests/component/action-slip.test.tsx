import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ActionSlip } from "@/components/chat/action-slip";
import type { Slip } from "@/lib/chat/conversation";

const actions = () => ({ confirm: vi.fn(), cancel: vi.fn(), otp: vi.fn(), identify: vi.fn(), checkApproval: vi.fn(), retry: vi.fn(), support: vi.fn() });
const future = new Date(Date.now() + 5 * 60_000).toISOString();

describe("ActionSlip", () => {
  it("confirmation shows amount and payee, with distinct cancel/confirm actions", async () => {
    const a = actions();
    const slip: Slip = { kind: "confirm", id: "1", actionId: "a", tool: "transfer_money", summary: "transfer ₹1,00,000 to Rahul", riskLevel: "CRITICAL",
      details: { amount: 100000, payee_name: "Rahul" }, expiresAt: future, state: "open" };
    render(<ActionSlip slip={slip} actions={a} />);
    expect(screen.getByRole("region", { name: /Transfer money confirmation/ })).toBeInTheDocument();
    expect(screen.getByText("₹1,00,000")).toBeInTheDocument();
    expect(screen.getByText("Rahul")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(a.cancel).toHaveBeenCalledOnce();
    expect(a.confirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm transfer" }));
    expect(a.confirm).toHaveBeenCalledOnce();
  });

  it("a resolved confirmation has no buttons", () => {
    const slip: Slip = { kind: "confirm", id: "1", actionId: "a", tool: "transfer_money", summary: "x", riskLevel: "CRITICAL", expiresAt: future, state: "cancelled" };
    render(<ActionSlip slip={slip} actions={actions()} />);
    expect(screen.queryByRole("button", { name: /Confirm/ })).toBeNull();
    expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
  });

  it("OTP entry is masked, validated and cleared after submit", async () => {
    const a = actions();
    render(<ActionSlip slip={{ kind: "verify", id: "v", step: "otp", purpose: "transaction", destination: "XXXXXX3210", requiredAuth: "TRANSACTION_AUTHENTICATED", state: "open" }} actions={a} />);
    const input = screen.getByLabelText("One-time password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "one-time-code");
    expect(screen.getByRole("button", { name: "Verify" })).toBeDisabled();
    await userEvent.type(input, "12ab3456");
    expect(input).toHaveValue("123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(a.otp).toHaveBeenCalledWith("123456");
    expect(input).toHaveValue("");
    expect(screen.getByText("Transaction authentication")).toBeInTheDocument();
  });

  it("failure copy depends on what the bank confirmed", () => {
    const { rerender } = render(<ActionSlip slip={{ kind: "failure", id: "f", tool: "transfer_money", outcome: "rejected", error: "insufficient balance", policyDecision: null }} actions={actions()} />);
    expect(screen.getByText(/No amount was debited/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    rerender(<ActionSlip slip={{ kind: "failure", id: "f", tool: "transfer_money", outcome: "unknown", error: "timeout", policyDecision: null }} actions={actions()} />);
    expect(screen.queryByText(/No amount was debited/)).toBeNull();
    expect(screen.getByText(/won't guess/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull(); // never invite a duplicate transfer
    rerender(<ActionSlip slip={{ kind: "failure", id: "f", tool: "transfer_money", outcome: "not_executed", error: "x", policyDecision: "DENY" }} actions={actions()} />);
    expect(screen.getByText("Not allowed by the bank's policy")).toBeInTheDocument();
  });

  it("receipt shows the transaction reference", () => {
    render(<ActionSlip slip={{ kind: "receipt", id: "r", tool: "transfer_money", latencyMs: 10, result: { status: "SUCCESS", transaction_ref: "IMPS77", amount: 5000, payee_name: "Rahul Verma", payee_account_masked: "XXXX4521" } }} actions={actions()} />);
    expect(screen.getByText("Transfer successful")).toBeInTheDocument();
    expect(screen.getByText("IMPS77")).toBeInTheDocument();
    expect(screen.getByText(/•••• 4521/)).toBeInTheDocument();
  });
});
