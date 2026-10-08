"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { AUTH_LABEL } from "@/components/ui/domain-badges";
import { Field, Input, Textarea } from "@/components/ui/input";
import { PageHeader, Panel, Section } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot } from "@/components/ui/status-dot";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import {
  AUTH_STATES, EFFECTS, conditionSchema, OPS, OP_LABEL, SESSION_STATS, describePolicy, describeRule, fromRule, isVisuallyEditable, policySchema,
  toRule, type Condition, type PolicyForm,
} from "@/lib/policies/schema";
import type { PolicyRule } from "@/types/domain";
import { formatDate, formatTime, titleCase } from "@/utils/format";

const conditionArraySchema = z.array(conditionSchema);
const statusOf = (r: PolicyRule) => (r.params.status === "draft" ? "draft" : r.enabled ? "active" : "disabled");
const STATUS_TONE = { active: "success", draft: "warning", disabled: "neutral" } as const;
const EFFECT_TONE: Record<string, "danger" | "warning" | "info" | "neutral"> = { DENY: "danger", REQUIRE_HUMAN_APPROVAL: "warning", REQUIRE_AUTH: "info", REQUIRE_CONFIRMATION: "info", SET_MIN_AUTH: "neutral" };

export function PolicyList() {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "policies"], queryFn: apiClient.policies.list });
  return (
    <PageContainer>
      <PageHeader title="Policies" description="Rules the policy engine applies to every tool call, in priority order, for chat and voice alike. The AI can't override them."
        actions={<Button variant="primary" asChild><Link href="/policies/new"><Plus />New policy</Link></Button>} />
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <SkeletonRows /> : q.data.effective.length + q.data.disabled.length === 0 ? (
        <EmptyState title="No policies" description="Add a rule such as requiring human approval for large transfers." />
      ) : (
        <ol className="grid gap-2">
          {[...q.data.effective, ...q.data.disabled].map((r) => (
            <li key={r.id}>
              <Link href={`/policies/${encodeURIComponent(r.id)}`} className="grid gap-2 rounded-panel border border-border bg-surface p-4 hover:border-border-strong hover:bg-hover sm:grid-cols-[48px_1fr_auto] sm:items-center">
                <span className="font-mono text-meta text-subtle" title="Priority">P{r.priority}</span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2"><span className="text-body font-medium text-strong">{r.name}</span>
                    {q.data.defaults.includes(r.id) && <Badge>Built-in</Badge>}</span>
                  <span className="mt-1 block text-small text-muted">{describeRule(r)}</span>
                </span>
                <span className="flex items-center gap-3"><Badge tone={EFFECT_TONE[r.effect]}>{titleCase(r.effect)}</Badge><StatusDot tone={STATUS_TONE[statusOf(r)]} label={titleCase(statusOf(r))} /></span>
              </Link>
            </li>
          ))}
        </ol>
      )}
      <p className="mt-4 text-small text-muted">Built-in rules can be overridden or disabled by saving a policy with the same ID. Drafts and disabled policies are listed but not enforced.</p>
    </PageContainer>
  );
}

const KINDS: { value: Condition["kind"]; label: string }[] = [
  { value: "tool", label: "Tool" }, { value: "channel", label: "Channel" }, { value: "intent", label: "Intent" },
  { value: "risk_level_gte", label: "Risk at least" }, { value: "auth_state_lt", label: "Authentication below" },
  { value: "arg", label: "Argument" }, { value: "session", label: "Session total" },
];
const INTENTS = ["KNOWLEDGE_QUERY", "CUSTOMER_DATA_QUERY", "ACTION_REQUEST", "FRAUD_REQUEST", "GENERAL_CONVERSATION", "HUMAN_HANDOFF"];

function blank(kind: Condition["kind"]): Condition {
  switch (kind) {
    case "tool": return { kind, value: "" };
    case "channel": return { kind, value: "voice" };
    case "intent": return { kind, value: "ACTION_REQUEST" };
    case "risk_level_gte": return { kind, value: "HIGH" };
    case "auth_state_lt": return { kind, value: "TRANSACTION_AUTHENTICATED" };
    case "arg": return { kind, name: "amount", op: "gt", value: "" };
    case "session": return { kind, name: "transfer_total_today", op: "gt", value: 0 };
  }
}

export function PolicyEditor({ id }: { id: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const router = useRouter();
  const { can } = useProfile();
  const isNew = id === "new";
  const list = useQuery({ queryKey: [t, "policies"], queryFn: apiClient.policies.list });
  const tools = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list });
  const history = useQuery({ queryKey: [t, "audit", "decisions"], queryFn: () => apiClient.audit.events({ event_type: "tool.decision", limit: 500 }), enabled: !isNew && can("audit.read") });
  const rule = list.data?.effective.find((r) => r.id === id) ?? list.data?.disabled.find((r) => r.id === id);
  if (!isNew && list.isLoading) return <PageContainer><SkeletonRows /></PageContainer>;
  if (!isNew && !rule) return <PageContainer><EmptyState title="Policy not found" description="It may belong to another organization." action={<Link href="/policies" className="text-small underline">Back to policies</Link>} /></PageContainer>;
  if (rule && !isVisuallyEditable(rule)) {
    return (
      <PageContainer>
        <PageHeader title={rule.name} description={describeRule(rule)} />
        <Panel className="p-4 text-small text-muted">This rule combines an argument with a running session total, which the visual editor doesn&apos;t support yet. Its definition:
          <pre className="mt-3 overflow-x-auto font-mono text-[12px] text-foreground">{JSON.stringify({ conditions: rule.conditions, effect: rule.effect, params: rule.params }, null, 2)}</pre></Panel>
        <DecisionHistory ruleId={rule.id} events={history.data} />
      </PageContainer>
    );
  }
  return <EditorForm key={id} initial={rule ? fromRule(rule) : undefined} isBuiltIn={Boolean(rule && list.data?.defaults.includes(rule.id))}
    toolNames={(tools.data ?? []).filter((x) => !x.internal).map((x) => x.name)} history={history.data} canEdit={can("policy.manage")}
    onSaved={() => { void qc.invalidateQueries({ queryKey: [t, "policies"] }); router.push("/policies"); }} />;
}

function EditorForm({ initial, isBuiltIn, toolNames, history, canEdit, onSaved }: {
  initial?: PolicyForm; isBuiltIn: boolean; toolNames: string[]; history?: Awaited<ReturnType<typeof apiClient.audit.events>>; canEdit: boolean; onSaved: () => void;
}) {
  const form = useForm<PolicyForm>({
    resolver: zodResolver(policySchema),
    defaultValues: initial ?? { name: "", priority: 50, conditions: [{ kind: "tool", value: "transfer_money" }, { kind: "arg", name: "amount", op: "gt", value: "100000" }],
                               effect: "REQUIRE_HUMAN_APPROVAL", reason: "", status: "draft" },
  });
  const { register, control, handleSubmit, formState: { errors } } = form;
  const conds = useFieldArray({ control, name: "conditions" });
  const watched = useWatch({ control });
  const [confirm, setConfirm] = useState<PolicyForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({ mutationFn: (f: PolicyForm) => apiClient.policies.upsert(toRule(f)), onSuccess: onSaved,
    onError: (e) => setError(e instanceof ApiError ? (e.detail ?? e.userMessage) : "Not saved.") });
  const preview = useMemo(() => {
    const conditions = conditionArraySchema.safeParse(watched.conditions);
    return conditions.success && conditions.data.length && watched.effect
      ? describePolicy({ conditions: conditions.data, effect: watched.effect, authState: watched.authState }) : null;
  }, [watched]);
  const effect = watched.effect;

  return (
    <PageContainer>
      <PageHeader title={initial ? initial.name : "New policy"} description={isBuiltIn ? "Built-in rule. Saving creates an override for your organization." : "Define when the policy engine steps in."} />
      <form className="grid gap-8 lg:grid-cols-[1fr_340px]" noValidate onSubmit={handleSubmit((f) => setConfirm(f))}>
        <fieldset disabled={!canEdit} className="grid content-start gap-6">
          <Panel className="grid gap-4 p-4 sm:grid-cols-[1fr_120px]">
            <Field label="Name" htmlFor="p-name" error={errors.name?.message}><Input id="p-name" aria-invalid={!!errors.name} {...register("name")} /></Field>
            <Field label="Priority" htmlFor="p-priority" hint="Lower runs first" error={errors.priority?.message}><Input id="p-priority" type="number" {...register("priority", { valueAsNumber: true })} /></Field>
            <Field label="Description" htmlFor="p-desc" className="sm:col-span-2"><Textarea id="p-desc" {...register("description")} className="min-h-14" /></Field>
          </Panel>
          <Section title="If" description="All conditions must match.">
            <Panel className="divide-y divide-border">
              {conds.fields.map((f, i) => {
                const kind = watched.conditions?.[i]?.kind ?? f.kind;
                const err = errors.conditions?.[i] as Record<string, { message?: string }> | undefined;
                return (
                  <div key={f.id} className="grid gap-2 p-3 sm:grid-cols-[170px_1fr_auto] sm:items-start">
                    <Controller control={control} name={`conditions.${i}.kind`} render={({ field }) => (
                      <Select aria-label={`Condition ${i + 1} type`} value={field.value} onValueChange={(v) => conds.update(i, blank(v as Condition["kind"]))} options={KINDS} />)} />
                    <div className="grid gap-2 sm:grid-cols-3">
                      {kind === "tool" && <Controller control={control} name={`conditions.${i}.value`} render={({ field }) => <Select aria-label="Tool" value={String(field.value ?? "")} onValueChange={field.onChange} placeholder="Choose a tool" className="sm:col-span-3" options={toolNames.map((n) => ({ value: n, label: n }))} />} />}
                      {kind === "channel" && <Controller control={control} name={`conditions.${i}.value`} render={({ field }) => <Select aria-label="Channel" value={String(field.value)} onValueChange={field.onChange} className="sm:col-span-3" options={[{ value: "chat", label: "Chat" }, { value: "voice", label: "Voice" }]} />} />}
                      {kind === "intent" && <Controller control={control} name={`conditions.${i}.value`} render={({ field }) => <Select aria-label="Intent" value={String(field.value)} onValueChange={field.onChange} className="sm:col-span-3" options={INTENTS.map((x) => ({ value: x, label: titleCase(x) }))} />} />}
                      {kind === "risk_level_gte" && <Controller control={control} name={`conditions.${i}.value`} render={({ field }) => <Select aria-label="Risk" value={String(field.value)} onValueChange={field.onChange} className="sm:col-span-3" options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((x) => ({ value: x, label: titleCase(x) }))} />} />}
                      {kind === "auth_state_lt" && <Controller control={control} name={`conditions.${i}.value`} render={({ field }) => <Select aria-label="Authentication" value={String(field.value)} onValueChange={field.onChange} className="sm:col-span-3" options={AUTH_STATES.map((x) => ({ value: x, label: AUTH_LABEL[x] }))} />} />}
                      {(kind === "arg" || kind === "session") && <>
                        {kind === "arg" ? <Input aria-label="Argument name" placeholder="amount" {...register(`conditions.${i}.name` as const)} />
                          : <Controller control={control} name={`conditions.${i}.name`} render={({ field }) => <Select aria-label="Session total" value={String(field.value)} onValueChange={field.onChange} options={SESSION_STATS.map((x) => ({ value: x, label: titleCase(x) }))} />} />}
                        <Controller control={control} name={`conditions.${i}.op`} render={({ field }) => <Select aria-label="Comparison" value={String(field.value)} onValueChange={field.onChange} options={OPS.map((o) => ({ value: o, label: OP_LABEL[o] }))} />} />
                        <Input aria-label="Value" aria-invalid={!!err?.value} placeholder="100000" {...register(`conditions.${i}.value` as const, kind === "session" ? { valueAsNumber: true } : {})} />
                      </>}
                      {(err?.value?.message || err?.name?.message) && <p role="alert" className="text-meta text-danger sm:col-span-3">{err?.value?.message ?? err?.name?.message}</p>}
                    </div>
                    <Button type="button" size="icon" variant="ghost" aria-label={`Remove condition ${i + 1}`} onClick={() => conds.remove(i)} disabled={conds.fields.length === 1}><Trash2 /></Button>
                  </div>
                );
              })}
              <div className="p-3"><Button type="button" size="sm" onClick={() => conds.append(blank("arg"))}><Plus />Add condition</Button></div>
            </Panel>
            {errors.conditions?.message && <p role="alert" className="text-meta text-danger">{errors.conditions.message}</p>}
          </Section>
          <Section title="Then">
            <Panel className="grid gap-4 p-4 sm:grid-cols-2">
              <Field label="Decision" htmlFor="p-effect"><Controller control={control} name="effect" render={({ field }) => <Select id="p-effect" value={field.value} onValueChange={field.onChange} options={EFFECTS.map((e) => ({ value: e, label: titleCase(e) }))} />} /></Field>
              {(effect === "REQUIRE_AUTH" || effect === "SET_MIN_AUTH") && <Field label="Authentication level" htmlFor="p-auth" error={errors.authState?.message}>
                <Controller control={control} name="authState" render={({ field }) => <Select id="p-auth" value={field.value} onValueChange={field.onChange} placeholder="Choose…" options={AUTH_STATES.map((a) => ({ value: a, label: AUTH_LABEL[a] }))} />} /></Field>}
              <Field label={effect === "DENY" ? "Reason shown to the customer" : "Reason (shown to operators)"} htmlFor="p-reason" error={errors.reason?.message} className="sm:col-span-2">
                <Input id="p-reason" aria-invalid={!!errors.reason} {...register("reason")} /></Field>
              <Field label="Status" htmlFor="p-status"><Controller control={control} name="status" render={({ field }) => <Select id="p-status" value={field.value} onValueChange={field.onChange} options={[{ value: "draft", label: "Draft (not enforced)" }, { value: "active", label: "Active" }, { value: "disabled", label: "Disabled" }]} />} /></Field>
            </Panel>
          </Section>
          {error && <p role="alert" className="text-small text-danger">{error}</p>}
          {canEdit && <div className="flex justify-end gap-2"><Button type="button" variant="ghost" asChild><Link href="/policies">Cancel</Link></Button><Button type="submit" variant="primary" loading={save.isPending}>Save policy</Button></div>}
        </fieldset>
        <aside className="grid content-start gap-6">
          <Section title="Reads as">
            <Panel className="p-4 font-mono text-[12.5px] leading-relaxed text-foreground">{preview ?? <span className="text-muted">Complete the conditions to see the rule.</span>}</Panel>
          </Section>
          {initial && <DecisionHistory ruleId={initial.id!} events={history} />}
        </aside>
      </form>
      <ConfirmDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} tone={confirm?.status === "active" ? "danger" : "primary"}
        title={confirm?.status === "active" ? "Enforce this policy now?" : "Save this policy?"}
        description={confirm ? describePolicy(confirm) : ""}
        consequence={confirm?.status === "active" ? "It applies to the next tool call on every channel. The change is audit-logged." : "Drafts and disabled policies aren't enforced."}
        confirmLabel={confirm?.status === "active" ? "Save and enforce" : "Save"} onConfirm={() => confirm && save.mutateAsync(confirm)} />
    </PageContainer>
  );
}

function DecisionHistory({ ruleId, events }: { ruleId: string; events?: Awaited<ReturnType<typeof apiClient.audit.events>> }) {
  const hits = (events ?? []).filter((e) => Array.isArray((e.payload as { rules?: unknown }).rules) && ((e.payload as { rules: string[] }).rules).includes(ruleId)).slice(0, 12);
  return (
    <Section title="Evaluation history" description="Recent tool calls where this rule matched.">
      <Panel className="divide-y divide-border">
        {events === undefined ? <p className="p-4 text-small text-muted">Requires audit access.</p> : hits.length === 0 ? <p className="p-4 text-small text-muted">No matches yet.</p> : hits.map((e) => (
          <div key={e.seq} className="grid gap-0.5 px-4 py-2.5">
            <p className="flex items-center justify-between gap-2 text-small"><span className="font-mono text-[12.5px]">{e.resource}</span><Badge tone={EFFECT_TONE[e.outcome] ?? "neutral"}>{titleCase(e.outcome)}</Badge></p>
            <p className="text-meta text-muted">{formatDate(e.occurred_at)} {formatTime(e.occurred_at)} · {e.channel}</p>
          </div>
        ))}
      </Panel>
    </Section>
  );
}
