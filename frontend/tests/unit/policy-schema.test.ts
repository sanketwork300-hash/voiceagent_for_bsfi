import { describe, expect, it } from "vitest";
import { describePolicy, describeRule, fromRule, policySchema, toRule, type PolicyForm } from "@/lib/policies/schema";
import type { PolicyRule } from "@/types/domain";

const base: PolicyForm = { name: "Large transfers need approval", priority: 30, effect: "REQUIRE_HUMAN_APPROVAL", status: "active", reason: "",
  conditions: [{ kind: "tool", value: "transfer_money" }, { kind: "arg", name: "amount", op: "gt", value: "100000" }] };

describe("policy editor model", () => {
  it("compiles to the backend DSL with numeric comparisons", () => {
    const r = toRule(base);
    expect(r.conditions).toEqual({ tool: "transfer_money", args: { amount: { gt: 100000 } } });
    expect(r.effect).toBe("REQUIRE_HUMAN_APPROVAL");
    expect(r.is_enabled).toBe(true);
    expect(r.id).toBe("tenant.large_transfers_need_approval");
  });
  it("drafts are saved disabled and round-trip", () => {
    const r = toRule({ ...base, status: "draft" });
    expect(r.is_enabled).toBe(false);
    const back = fromRule({ ...r, enabled: r.is_enabled } as PolicyRule);
    expect(back.status).toBe("draft");
    expect(back.conditions).toEqual(base.conditions);
  });
  it("multiple tool conditions become a list", () => {
    const r = toRule({ ...base, conditions: [{ kind: "tool", value: "block_card" }, { kind: "tool", value: "transfer_money" }] });
    expect(r.conditions.tool).toEqual(["block_card", "transfer_money"]);
  });
  it("validates denials need a customer-facing reason and auth effects need a level", () => {
    expect(policySchema.safeParse({ ...base, effect: "DENY" }).success).toBe(false);
    expect(policySchema.safeParse({ ...base, effect: "REQUIRE_AUTH" }).success).toBe(false);
    expect(policySchema.safeParse({ ...base, effect: "REQUIRE_AUTH", authState: "TRANSACTION_AUTHENTICATED" }).success).toBe(true);
    expect(policySchema.safeParse({ ...base, conditions: [] }).success).toBe(false);
    expect(policySchema.safeParse({ ...base, conditions: [{ kind: "arg", name: "amount", op: "gt", value: "lots" }] }).success).toBe(false);
  });
  it("reads as plain language", () => {
    expect(describePolicy(base)).toBe("If tool is transfer_money and amount > ₹1,00,000, then require human approval.");
    expect(describeRule({ id: "d", name: "cap", priority: 20, effect: "DENY", enabled: true, params: {},
      conditions: { tool: "transfer_money", args_plus_session: { arg: "amount", stat: "transfer_total_today", gt: 1000000 } } }))
      .toBe("If tool is transfer_money and amount + transfer total today > ₹10,00,000, then deny the action.");
  });
});
