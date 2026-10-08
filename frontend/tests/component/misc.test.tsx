import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthLadder } from "@/components/authentication/auth-ladder";
import { Composer } from "@/components/chat/composer";
import { MessageView } from "@/components/chat/message";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Keypad } from "@/components/voice/keypad";
import type { ChatMessage } from "@/lib/chat/conversation";

const noop = () => {};
const slipActions = { confirm: noop, cancel: noop, otp: noop, identify: noop, checkApproval: noop, retry: noop, support: noop };
const msg = (p: Partial<ChatMessage>): ChatMessage => ({ id: "m", role: "agent", text: "", at: "2026-10-07T10:00:00Z", status: "done", tools: [], sources: [], slips: [], intent: null, ...p });

describe("ConfirmDialog", () => {
  it("focuses Cancel by default and only confirms on the explicit button", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onOpenChange={noop} title="Block this card?" description="It can't be undone." confirmLabel="Block card" onConfirm={onConfirm} />);
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.keyboard("{Enter}"); // Enter on the focused Cancel must not confirm
    expect(onConfirm).not.toHaveBeenCalled();
  });
  it("runs the action when confirmed", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onOpenChange={noop} title="t" description="d" confirmLabel="Block card" onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Block card" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("AuthLadder", () => {
  it("never shows transaction authentication for a session-level login", () => {
    render(<AuthLadder state="FULLY_AUTHENTICATED" />);
    expect(screen.getByText("Session authenticated").parentElement).toHaveTextContent("Verified");
    expect(screen.getByText("Transaction authenticated").parentElement).toHaveTextContent("Pending");
  });
  it("marks the next step as failed after a failed attempt", () => {
    render(<AuthLadder state="IDENTIFIED" failed />);
    expect(screen.getByText("Partially verified").parentElement).toHaveTextContent("Failed");
  });
});

describe("Keypad", () => {
  it("masks digits and submits them", async () => {
    const onSubmit = vi.fn();
    render(<Keypad onSubmit={onSubmit} onClose={noop} />);
    for (const d of "4821") await userEvent.click(screen.getByRole("button", { name: `Digit ${d}` }));
    expect(screen.getByText("••••")).toBeInTheDocument();
    expect(screen.queryByText("4821")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Send code" }));
    expect(onSubmit).toHaveBeenCalledWith("4821");
  });
});

describe("MessageView", () => {
  it("renders markdown safely: raw HTML is not executed", () => {
    render(<MessageView m={msg({ text: 'Hello <img src=x onerror="window.__xss=1"> **bold** [1]' })} audience="customer" slipActions={slipActions} busy={false} />);
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "Source 1" })).toHaveAttribute("href", "#source-1");
  });
  it("shows customers services used, not tool internals", () => {
    render(<MessageView m={msg({ text: "Balance", tools: [{ id: "t", tool: "get_account_balance", status: "completed", latencyMs: 12, source: "openapi" }] })}
      audience="customer" slipActions={slipActions} busy={false} />);
    expect(screen.getByText("Account service")).toBeInTheDocument();
    expect(screen.queryByText(/get_account_balance/)).toBeNull();
  });
  it("operators can expand the tool trace", async () => {
    render(<MessageView m={msg({ text: "Balance", tools: [{ id: "t", tool: "get_account_balance", status: "completed", latencyMs: 182, source: "openapi" }] })}
      audience="operator" slipActions={slipActions} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: /1 tool call/ }));
    expect(screen.getByText("182 ms")).toBeInTheDocument();
    expect(screen.getByText("Bank API (OpenAPI)")).toBeInTheDocument();
  });
  it("masked customer messages never show their content", () => {
    render(<MessageView m={msg({ role: "customer", text: "123456", masked: true })} audience="customer" slipActions={slipActions} busy={false} />);
    expect(screen.queryByText("123456")).toBeNull();
    expect(screen.getByText("One-time password entered")).toBeInTheDocument();
  });
});

describe("Composer", () => {
  it("Enter sends, Shift+Enter adds a line, empty input can't be sent", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} busy={false} />);
    const box = screen.getByLabelText("Message");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("line one\nline two");
    expect(box).toHaveValue("");
  });
});
