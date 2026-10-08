import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const upsert = vi.fn().mockResolvedValue({ id: "tenant.x" });
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/client", () => ({
  apiClient: {
    auth: { me: async () => ({ profile: { user_id: "u", email: "a@b", tenant_id: "t", tenant_slug: "demo-bank", roles: ["tenant_admin"], expires_at: 9e9 } }) },
    policies: { list: async () => ({ effective: [], defaults: [], disabled: [] }), upsert: (...a: unknown[]) => upsert(...a) },
    tools: { list: async () => [{ name: "transfer_money", internal: false }] },
    audit: { events: async () => [] },
  },
}));

import { PolicyEditor } from "@/components/policies/policies";

const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

describe("PolicyEditor", () => {
  it("previews the rule in plain language and blocks saving without a name", async () => {
    wrap(<PolicyEditor id="new" />);
    expect(await screen.findByText("If tool is transfer_money and amount > ₹1,00,000, then require human approval.")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Give the policy a descriptive name")).toBeInTheDocument();
    expect(upsert).not.toHaveBeenCalled();
  });

  it("requires explicit confirmation before saving", async () => {
    wrap(<PolicyEditor id="new" />);
    await userEvent.type(await screen.findByLabelText("Name"), "Big transfers need a checker");
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("Save this policy?");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({ name: "Big transfers need a checker", effect: "REQUIRE_HUMAN_APPROVAL",
      conditions: { tool: "transfer_money", args: { amount: { gt: 100000 } } }, is_enabled: false }));
  });
});
