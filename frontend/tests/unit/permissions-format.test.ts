import { describe, expect, it } from "vitest";
import { messageForStatus } from "@/lib/api/errors";
import { can, primaryRole } from "@/lib/permissions";
import { formatINR, languageLabel, maskId, titleCase } from "@/utils/format";

describe("permissions mirror the backend RBAC", () => {
  it("maker-checker: admins can't approve, supervisors can", () => {
    expect(can(["tenant_admin"], "approval.execute")).toBe(false);
    expect(can(["supervisor"], "approval.execute")).toBe(true);
  });
  it("human agents handle handoffs but can't touch policies, audit or integrations", () => {
    for (const p of ["policy.manage", "audit.read", "integration.manage", "tool.configure"] as const) expect(can(["human_agent"], p)).toBe(false);
    expect(can(["human_agent"], "handoff.handle")).toBe(true);
  });
  it("auditors read audit logs only", () => {
    expect(can(["auditor"], "audit.read")).toBe(true);
    expect(can(["auditor"], "agent.manage")).toBe(false);
  });
  it("unknown roles and missing roles grant nothing", () => {
    expect(can(["root"], "audit.read")).toBe(false);
    expect(can(undefined, "audit.read")).toBe(false);
    expect(primaryRole(["human_agent", "tenant_admin"])).toBe("tenant_admin");
  });
});

describe("formatting", () => {
  it("uses Indian digit grouping", () => {
    expect(formatINR(2845000)).toBe("₹28,45,000");
    expect(formatINR(100000)).toBe("₹1,00,000");
    expect(formatINR(1172.5)).toBe("₹1,172.5");
    expect(formatINR("nope")).toBe("—");
  });
  it("never reveals more than the last four characters", () => {
    expect(maskId("4111111111114242")).toBe("•••• 4242");
    expect(maskId("XXXX4521")).toBe("•••• 4521");
    expect(maskId("123")).toBe("••••");
  });
  it("labels Hinglish separately from Hindi", () => {
    expect(languageLabel("hi", "hi-Latn")).toBe("Hinglish");
    expect(languageLabel("hi", "hi")).toBe("Hindi");
    expect(titleCase("REQUIRE_HUMAN_APPROVAL")).toBe("Require human approval");
  });
  it("error copy never exposes status codes and says nothing changed on outages", () => {
    expect(messageForStatus(500)).not.toMatch(/500|internal/i);
    expect(messageForStatus(503)).toMatch(/Nothing was changed/);
    expect(messageForStatus(0)).toMatch(/connection/);
  });
});
