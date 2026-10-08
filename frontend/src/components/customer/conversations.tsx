"use client";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { TranscriptView } from "@/components/chat/transcript-view";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Input } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import type { ConversationSummary } from "@/types/domain";
import { formatDate, formatTime, languageLabel, maskId, titleCase } from "@/utils/format";

const WINDOWS = { today: 1, "7d": 7, "30d": 30, all: 3650 } as const;

export function ConversationsList({ mode }: { mode: "conversations" | "sessions" }) {
  const t = useTenantKey();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [win, setWin] = useState<keyof typeof WINDOWS>("7d");
  const [now] = useState(() => Date.now());
  const list = useQuery({ queryKey: [t, "conversations"], queryFn: () => apiClient.conversations.list(200), refetchInterval: mode === "sessions" ? 10_000 : false });
  const rows = useMemo(() => {
    const since = now - WINDOWS[win] * 86400_000;
    return (list.data ?? []).filter((c) => new Date(c.created_at).getTime() >= since)
      .filter((c) => mode === "conversations" || c.status === "ACTIVE")
      .filter((c) => !q || `${c.id} ${c.customer_ref ?? ""} ${c.last_intent ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  }, [list.data, q, win, mode, now]);
  const stats = useMemo(() => {
    const all = list.data ?? [];
    return { total: all.length, voice: all.filter((c) => c.channels_used.includes("voice")).length, switched: all.filter((c) => c.channels_used.length > 1).length };
  }, [list.data]);
  const columns: Column<ConversationSummary>[] = [
    { key: "id", header: "Conversation", primary: true, cell: (c) => <span className="font-mono text-[12.5px]">{c.id.slice(0, 8)}</span>, sort: (c) => c.id },
    { key: "customer", header: "Customer", cell: (c) => c.customer_ref ? <span className="font-mono text-[12.5px]">{maskId(c.customer_ref)}</span> : <span className="text-muted">Anonymous</span> },
    { key: "channels", header: "Channels", cell: (c) => <span className="flex gap-1">{c.channels_used.map((ch) => <Badge key={ch} tone={ch === "voice" ? "voice" : "neutral"}>{ch}</Badge>)}</span> },
    { key: "intent", header: "Last intent", cell: (c) => (c.last_intent ? titleCase(c.last_intent) : <span className="text-muted">—</span>) },
    { key: "language", header: "Language", cell: (c) => languageLabel(c.language) },
    { key: "status", header: "Status", cell: (c) => <Badge tone={c.status === "ACTIVE" ? "success" : "neutral"}>{titleCase(c.status)}</Badge> },
    { key: "created", header: "Started", cell: (c) => <span className="tabular text-muted">{formatDate(c.created_at)} {formatTime(c.created_at, false)}</span>, sort: (c) => c.created_at },
  ];
  return (
    <PageContainer>
      <PageHeader title={mode === "sessions" ? "Sessions" : "Conversations"}
        description={mode === "sessions" ? "Live customer sessions. A session can move between chat and voice without losing context." : "Every conversation across chat and voice, with full transcripts."}
        meta={<span>{stats.total} total · {stats.voice} used voice · {stats.switched} switched channel</span>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input aria-label="Filter conversations" placeholder="Filter by ID, customer or intent" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <div role="group" aria-label="Date range" className="flex rounded-control border border-border-strong p-0.5">
          {(Object.keys(WINDOWS) as (keyof typeof WINDOWS)[]).map((w) => <button key={w} aria-pressed={win === w} onClick={() => setWin(w)} className={`rounded-[4px] px-2.5 py-1 text-small ${win === w ? "bg-raised text-strong" : "text-muted"}`}>{w === "all" ? "All" : w === "today" ? "Today" : w}</button>)}
        </div>
      </div>
      <DataTable caption="Conversations" rows={list.data ? rows : undefined} error={list.error} onRetry={() => list.refetch()} columns={columns} getKey={(c) => c.id}
        onRowClick={(c) => router.push(`/conversations/${c.id}`)}
        empty={{ title: "No conversations yet", description: "Once customers interact with your AI agent, their conversations will appear here." }} />
    </PageContainer>
  );
}

export function ConversationDetail({ id }: { id: string }) {
  const t = useTenantKey();
  const msgs = useQuery({ queryKey: [t, "conversation", id], queryFn: () => apiClient.conversations.messages(id) });
  const list = useQuery({ queryKey: [t, "conversations"], queryFn: () => apiClient.conversations.list(200) });
  const summary = list.data?.find((c) => c.id === id);
  const tools = (msgs.data ?? []).flatMap((m) => m.tool_calls);
  return (
    <PageContainer>
      <PageHeader title={`Conversation ${id.slice(0, 8)}`} description="Full transcript with tool calls and knowledge sources. Authentication secrets are never stored."
        meta={summary && <>{summary.channels_used.map((c) => <Badge key={c} tone={c === "voice" ? "voice" : "neutral"}>{c}</Badge>)}<span>{formatDate(summary.created_at)}</span></>} />
      <div className="grid gap-8 lg:grid-cols-[1fr_300px]">
        <Section title="Transcript">
          {msgs.error ? <ErrorState error={msgs.error} onRetry={() => msgs.refetch()} /> : !msgs.data ? <SkeletonRows /> : <TranscriptView messages={msgs.data} />}
        </Section>
        <aside className="grid content-start gap-6">
          <Section title="Summary">
            <Panel className="p-4">
              <KeyValue items={[
                { label: "Customer", value: summary?.customer_ref ? maskId(summary.customer_ref) : "Anonymous", mono: true },
                { label: "Messages", value: msgs.data?.length ?? "—" },
                { label: "Tool calls", value: tools.length },
                { label: "Blocked", value: tools.filter((x) => x.status === "denied").length },
                { label: "Language", value: languageLabel(summary?.language) },
                { label: "Last intent", value: summary?.last_intent ? titleCase(summary.last_intent) : "—" },
              ]} />
            </Panel>
          </Section>
        </aside>
      </div>
    </PageContainer>
  );
}
