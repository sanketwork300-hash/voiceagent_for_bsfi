import { z } from "zod";
import type { PolicyRule } from "@/types/domain";

/** Visual policy editor model. Compiles to the backend rule DSL (backend/app/policies/rules.py). */
export const OPS = ["gt", "gte", "lt", "lte", "eq", "ne"] as const;
export type Op = (typeof OPS)[number];
export const OP_LABEL: Record<Op, string> = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=", ne: "≠" };
export const EFFECTS = ["DENY", "REQUIRE_AUTH", "REQUIRE_CONFIRMATION", "REQUIRE_HUMAN_APPROVAL", "SET_MIN_AUTH"] as const;
export const AUTH_STATES = ["UNAUTHENTICATED", "IDENTIFIED", "PARTIALLY_AUTHENTICATED", "FULLY_AUTHENTICATED", "TRANSACTION_AUTHENTICATED"] as const;
export const SESSION_STATS = ["transfer_total_today", "transfer_count_today", "auth_failures", "policy_rejections"] as const;

export const conditionSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("tool"), value: z.string().min(1, "Choose a tool") }),
  z.object({ kind: z.literal("channel"), value: z.enum(["chat", "voice"]) }),
  z.object({ kind: z.literal("intent"), value: z.string().min(1, "Choose an intent") }),
  z.object({ kind: z.literal("risk_level_gte"), value: z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]) }),
  z.object({ kind: z.literal("auth_state_lt"), value: z.enum(AUTH_STATES) }),
  z.object({ kind: z.literal("arg"), name: z.string().regex(/^[a-z_][a-z0-9_]*$/, "Use a parameter name like amount"), op: z.enum(OPS), value: z.string().min(1, "Enter a value") }),
  z.object({ kind: z.literal("session"), name: z.enum(SESSION_STATS), op: z.enum(OPS), value: z.number({ message: "Enter a number" }) }),
]);
export type Condition = z.infer<typeof conditionSchema>;

export const policySchema = z.object({
  id: z.string().optional(),
  name: z.string().trim().min(3, "Give the policy a descriptive name"),
  description: z.string().max(500).optional(),
  priority: z.number().int().min(0).max(1000),
  conditions: z.array(conditionSchema).min(1, "Add at least one condition"),
  effect: z.enum(EFFECTS),
  reason: z.string().max(200).optional(),
  authState: z.enum(AUTH_STATES).optional(),
  status: z.enum(["active", "draft", "disabled"]),
}).superRefine((p, ctx) => {
  if ((p.effect === "REQUIRE_AUTH" || p.effect === "SET_MIN_AUTH") && !p.authState) ctx.addIssue({ code: "custom", path: ["authState"], message: "Choose the authentication level" });
  if (p.effect === "DENY" && !p.reason) ctx.addIssue({ code: "custom", path: ["reason"], message: "Explain the denial — customers see this reason" });
  for (const [i, c] of p.conditions.entries()) {
    if (c.kind === "arg" && ["gt", "gte", "lt", "lte"].includes(c.op) && Number.isNaN(Number(c.value))) {
      ctx.addIssue({ code: "custom", path: ["conditions", i, "value"], message: "Comparisons need a number" });
    }
  }
});
export type PolicyForm = z.infer<typeof policySchema>;

const asNum = (v: string) => (v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : v);

export function toRule(f: PolicyForm): Omit<PolicyRule, "enabled"> & { is_enabled: boolean; description?: string } {
  const conditions: PolicyRule["conditions"] = {};
  const multi = (key: "tool" | "channel" | "intent", v: string) => {
    const cur = conditions[key];
    conditions[key] = cur === undefined ? v : Array.isArray(cur) ? [...cur, v] : [cur, v];
  };
  for (const c of f.conditions) {
    if (c.kind === "tool" || c.kind === "channel" || c.kind === "intent") multi(c.kind, c.value);
    else if (c.kind === "risk_level_gte") conditions.risk_level_gte = c.value;
    else if (c.kind === "auth_state_lt") conditions.auth_state_lt = c.value;
    else if (c.kind === "arg") conditions.args = { ...conditions.args, [c.name]: { ...conditions.args?.[c.name], [c.op]: asNum(c.value) } };
    else conditions.session = { ...conditions.session, [c.name]: { ...conditions.session?.[c.name], [c.op]: c.value } };
  }
  const params: Record<string, unknown> = { status: f.status };
  if (f.reason) params.reason = f.reason;
  if (f.effect === "REQUIRE_AUTH" && f.authState) params.required_auth_state = f.authState;
  if (f.effect === "SET_MIN_AUTH" && f.authState) params.min_auth_state = f.authState;
  return {
    id: f.id || `tenant.${f.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 48)}`, name: f.name, priority: f.priority,
    conditions, effect: f.effect, params, is_enabled: f.status === "active", description: f.description,
  };
}

export function fromRule(r: PolicyRule): PolicyForm {
  const conds: Condition[] = [];
  const list = (v: string | string[] | undefined) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
  for (const v of list(r.conditions.tool)) conds.push({ kind: "tool", value: v });
  for (const v of list(r.conditions.channel)) conds.push({ kind: "channel", value: v as "chat" | "voice" });
  for (const v of list(r.conditions.intent)) conds.push({ kind: "intent", value: v });
  if (r.conditions.risk_level_gte) conds.push({ kind: "risk_level_gte", value: r.conditions.risk_level_gte });
  if (r.conditions.auth_state_lt) conds.push({ kind: "auth_state_lt", value: r.conditions.auth_state_lt });
  for (const [name, spec] of Object.entries(r.conditions.args ?? {})) {
    for (const [op, v] of Object.entries(spec)) if ((OPS as readonly string[]).includes(op)) conds.push({ kind: "arg", name, op: op as Op, value: String(v) });
  }
  for (const [name, spec] of Object.entries(r.conditions.session ?? {})) {
    for (const [op, v] of Object.entries(spec)) {
      if ((OPS as readonly string[]).includes(op) && (SESSION_STATS as readonly string[]).includes(name)) {
        conds.push({ kind: "session", name: name as (typeof SESSION_STATS)[number], op: op as Op, value: Number(v) });
      }
    }
  }
  const status = (r.params.status as PolicyForm["status"]) ?? (r.enabled ? "active" : "disabled");
  return {
    id: r.id, name: r.name, priority: r.priority, conditions: conds, effect: r.effect, reason: (r.params.reason as string) ?? "",
    authState: (r.params.required_auth_state ?? r.params.min_auth_state) as PolicyForm["authState"], status,
  };
}

/** Rules using the combined "argument + session total" condition can't be represented in the visual editor yet. */
export function isVisuallyEditable(r: PolicyRule): boolean {
  return !r.conditions.args_plus_session && !r.conditions.source;
}

const INR = (v: unknown) => (typeof v === "number" ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(v) : String(v));
const EFFECT_TEXT: Record<(typeof EFFECTS)[number], string> = {
  DENY: "deny the action", REQUIRE_AUTH: "require authentication", REQUIRE_CONFIRMATION: "require customer confirmation",
  REQUIRE_HUMAN_APPROVAL: "require human approval", SET_MIN_AUTH: "set the minimum authentication",
};

export function describeCondition(c: Condition): string {
  switch (c.kind) {
    case "tool": return `tool is ${c.value}`;
    case "channel": return `channel is ${c.value}`;
    case "intent": return `intent is ${c.value.toLowerCase().replace(/_/g, " ")}`;
    case "risk_level_gte": return `risk is ${c.value.toLowerCase()} or higher`;
    case "auth_state_lt": return `authentication is below ${c.value.toLowerCase().replace(/_/g, " ")}`;
    case "arg": return `${c.name} ${OP_LABEL[c.op]} ${/amount/.test(c.name) ? INR(asNum(c.value)) : c.value}`;
    case "session": return `${c.name.replace(/_/g, " ")} ${OP_LABEL[c.op]} ${/total/.test(c.name) ? INR(c.value) : c.value}`;
  }
}

export function describeRule(r: PolicyRule): string {
  if (r.conditions.args_plus_session) {
    const a = r.conditions.args_plus_session;
    const op = OPS.find((o) => a[o] !== undefined);
    const tool = r.conditions.tool ? `tool is ${[r.conditions.tool].flat().join(" or ")} and ` : "";
    return `If ${tool}${a.arg} + ${a.stat.replace(/_/g, " ")} ${op ? OP_LABEL[op] : ""} ${op ? INR(a[op]) : ""}, then ${EFFECT_TEXT[r.effect]}.`;
  }
  return describePolicy(fromRule(r));
}

export function describePolicy(f: Pick<PolicyForm, "conditions" | "effect" | "authState">): string {
  const cond = f.conditions.map(describeCondition).join(" and ") || "always";
  const then = EFFECT_TEXT[f.effect] + (f.authState && (f.effect === "REQUIRE_AUTH" || f.effect === "SET_MIN_AUTH") ? ` (${f.authState.toLowerCase().replace(/_/g, " ")})` : "");
  return `If ${cond}, then ${then}.`;
}
