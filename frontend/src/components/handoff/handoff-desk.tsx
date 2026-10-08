"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Headset } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AuthBadge } from "@/components/ui/domain-badges";
import { Textarea } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Handoff } from "@/types/domain";
import { formatRelative, maskId, titleCase } from "@/utils/format";

const PRIORITY_TONE = { urgent: "danger", high: "warning", normal: "neutral" } as const;

function HandoffCard({ h, selected, onSelect }: { h: Handoff; selected: boolean; onSelect: () => void }) {
  return (
    <button onClick={onSelect} aria-pressed={selected} className={`grid w-full gap-1 border-b border-border px-4 py-3 text-left hover:bg-hover ${selected ? "bg-raised shadow-[inset_2px_0_0_var(--strong)]" : ""}`}>
      <span className="flex items-center gap-2"><Badge tone={PRIORITY_TONE[h.priority]}>{titleCase(h.priority)}</Badge><span className="text-small text-foreground">{titleCase(h.reason)}</span></span>
      <span className="line-clamp-2 text-meta text-muted">{h.context.summary}</span>
      <span className="text-meta text-subtle">{h.channel} · session <span className="font-mono">{h.session_id.slice(-8)}</span> · {formatRelative(h.created_at)}</span>
    </button>
  );
}

export function HandoffDesk({ title = "Human handoff", description = "Customers waiting for a specialist, with the context the AI collected." }: { title?: string; description?: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"queued" | "assigned" | "resolved">("queued");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const q = useQuery({ queryKey: [t, "handoff", status], queryFn: () => apiClient.handoff.queue(status), refetchInterval: 5000 });
  const selected = q.data?.find((h) => h.id === selectedId) ?? q.data?.[0] ?? null;
  const refresh = () => qc.invalidateQueries({ queryKey: [t, "handoff"] });
  const onErr = (e: unknown) => setError(e instanceof ApiError ? e.userMessage : "That didn't work. Try again.");
  const accept = useMutation({ mutationFn: (id: string) => apiClient.handoff.accept(id), onSuccess: () => { setStatus("assigned"); void refresh(); }, onError: onErr });
  const send = useMutation({ mutationFn: ({ id, text }: { id: string; text: string }) => apiClient.handoff.reply(id, text), onSuccess: () => setReply(""), onError: onErr });
  const resolve = useMutation({ mutationFn: ({ id, back }: { id: string; back: boolean }) => apiClient.handoff.resolve(id, back ? "Returned to AI assistant" : "Resolved by specialist", back), onSuccess: () => { setSelectedId(null); void refresh(); }, onError: onErr });

  return (
    <PageContainer wide>
      <PageHeader title={title} description={description} />
      <Tabs value={status} onValueChange={(v) => { setStatus(v as typeof status); setSelectedId(null); }}>
        <TabsList><TabsTrigger value="queued">Waiting</TabsTrigger><TabsTrigger value="assigned">In progress</TabsTrigger><TabsTrigger value="resolved">Resolved</TabsTrigger></TabsList>
        <TabsContent value={status}>
          {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <SkeletonRows /> : q.data.length === 0 ? (
            <EmptyState icon={<Headset />} title={status === "queued" ? "No one is waiting" : status === "assigned" ? "Nothing in progress" : "No resolved handoffs yet"}
              description="When the AI escalates a conversation — fraud, failed verification, or a customer asking for a person — it appears here with the full context." />
          ) : (
            <div className="grid overflow-hidden rounded-panel border border-border lg:grid-cols-[340px_1fr]">
              <div className="max-h-[70vh] overflow-y-auto border-b border-border lg:border-b-0 lg:border-r">{q.data.map((h) => <HandoffCard key={h.id} h={h} selected={selected?.id === h.id} onSelect={() => setSelectedId(h.id)} />)}</div>
              {selected && (
                <div className="grid content-start gap-6 p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div><p className="text-section font-medium text-strong">{titleCase(selected.reason)}</p><p className="text-small text-muted">{selected.channel === "voice" ? "Voice call" : "Chat"} · opened {formatRelative(selected.created_at)}</p></div>
                    <div className="flex gap-2">
                      {selected.status === "queued" && <Button variant="primary" loading={accept.isPending} onClick={() => accept.mutate(selected.id)}>Accept handoff</Button>}
                      {selected.status === "assigned" && <>
                        <Button onClick={() => resolve.mutate({ id: selected.id, back: true })} loading={resolve.isPending}>Return to AI</Button>
                        <Button variant="primary" onClick={() => resolve.mutate({ id: selected.id, back: false })} loading={resolve.isPending}><Check />Mark resolved</Button>
                      </>}
                    </div>
                  </div>
                  {error && <p role="alert" className="text-small text-danger">{error}</p>}
                  <Section title="AI summary"><Panel className="p-4 text-body">{selected.context.summary || "No summary."}</Panel></Section>
                  <div className="grid gap-6 xl:grid-cols-2">
                    <Section title="Context shared">
                      <Panel className="p-4">
                        <KeyValue items={[
                          { label: "Customer", value: selected.context.customer_id ? maskId(selected.context.customer_id) : "Not identified", mono: true },
                          { label: "Verification", value: <AuthBadge state={selected.context.authentication_status} /> },
                          { label: "Intent", value: selected.context.intent ? titleCase(selected.context.intent) : "—" },
                          { label: "Language", value: selected.context.language },
                          { label: "Note", value: selected.context.note ?? "—" },
                        ]} />
                        <Link href={`/conversations/${selected.conversation_id}`} className="mt-3 inline-block text-small underline underline-offset-2">Open full transcript</Link>
                      </Panel>
                    </Section>
                    <Section title="What the AI did">
                      <Panel className="p-4">
                        {selected.context.tools_called.length === 0 ? <p className="text-small text-muted">No tools were called.</p> : (
                          <ul className="grid gap-1.5">{selected.context.tools_called.map((tc, i) => <li key={i} className="flex justify-between gap-3 text-small"><span className="font-mono text-[12.5px]">{tc.tool}</span><span className="text-muted">{tc.status}{tc.decision && tc.decision !== "ALLOW" ? ` · ${titleCase(tc.decision)}` : ""}</span></li>)}</ul>
                        )}
                        {selected.context.actions_taken.length > 0 && <p className="mt-3 text-meta text-warning">{selected.context.actions_taken.length} financial action(s) completed before handoff.</p>}
                      </Panel>
                    </Section>
                  </div>
                  {selected.status === "assigned" && (
                    <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (reply.trim()) send.mutate({ id: selected.id, text: reply.trim() }); }}>
                      <label htmlFor="desk-reply" className="text-small font-medium text-strong">Reply to the customer</label>
                      <Textarea id="desk-reply" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Your message appears in the customer's conversation." maxLength={2000} />
                      <div className="flex justify-end"><Button type="submit" variant="primary" loading={send.isPending} disabled={!reply.trim()}>Send reply</Button></div>
                    </form>
                  )}
                </div>
              )}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
