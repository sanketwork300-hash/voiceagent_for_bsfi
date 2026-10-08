"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Metric, MetricStrip } from "@/components/ui/metrics";
import { PageHeader, Panel, Section } from "@/components/ui/page";
import { ErrorState, Skeleton, WidgetBoundary } from "@/components/ui/states";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import type { MonitoringSummary } from "@/types/domain";
import { formatMs, formatPercent, titleCase } from "@/utils/format";
import { ActivityStream } from "./activity-stream";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export function useSummary(hours: number) {
  const t = useTenantKey();
  return useQuery({ queryKey: [t, "monitoring", hours], queryFn: () => apiClient.monitoring.summary(hours), refetchInterval: 15_000 });
}

function KpiStrip({ s }: { s: MonitoringSummary }) {
  const series = s.series.map((p) => p.calls);
  const lat = s.series.map((p) => p.latency_ms ?? 0);
  return (
    <MetricStrip>
      <Metric label="Active sessions" value={s.active_sessions.total} sub={`${s.active_sessions.chat} chat · ${s.active_sessions.voice} voice`} />
      <Metric label="Resolved without a person" value={formatPercent(s.conversations.total ? s.conversations.resolved_without_escalation / s.conversations.total : null)} sub={`${s.conversations.total} conversations`} />
      <Metric label="Human handoff rate" value={formatPercent(s.handoffs.rate)} sub={`${s.handoffs.total} handoffs`} />
      <Metric label="Tool success" value={formatPercent(s.tools.success_rate)} sub={`${s.tools.total} calls`} series={series} tone={(s.tools.success_rate ?? 1) < 0.95 ? "warning" : undefined} />
      <Metric label="Tool latency p95" value={formatMs(s.tools.latency_ms.p95)} sub={`avg ${formatMs(s.tools.latency_ms.avg)}`} series={lat} />
      <Metric label="Knowledge grounding" value={formatPercent(s.knowledge_grounding)} sub={s.last_evaluation ? "from last evaluation run" : "run an evaluation to measure"} />
      <Metric label="Authentication success" value={formatPercent(s.authentication.success_rate)} sub={`${s.authentication.otp_failed} failed OTP${s.authentication.otp_failed === 1 ? "" : "s"}`} tone={s.authentication.otp_failed >= 3 ? "warning" : undefined} />
      <Metric label="Failed or blocked actions" value={s.tools.failed + s.tools.denied} sub={`${s.tools.failed} failed · ${s.tools.denied} blocked by policy`} tone={s.tools.failed ? "danger" : undefined} />
    </MetricStrip>
  );
}

export function Dashboard() {
  const { profile } = useProfile();
  const q = useSummary(24);
  return (
    <PageContainer>
      <PageHeader title={`${greeting()}.`} description="AI operations overview for the last 24 hours."
        meta={profile?.email ? <span>Signed in as {profile.email}</span> : undefined} />
      <div className="grid gap-8">
        <WidgetBoundary title="Metrics">
          {q.error ? <ErrorState error={q.error} title="Metrics unavailable" onRetry={() => q.refetch()} />
            : !q.data ? <Skeleton className="h-48 w-full" /> : <KpiStrip s={q.data} />}
        </WidgetBoundary>
        <div className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
          <Section title="Live activity" description="Updates every few seconds." actions={<Link href="/audit" className="text-small text-muted hover:text-foreground">Audit log</Link>}>
            <Panel className="px-4"><WidgetBoundary title="Live activity"><ActivityStream /></WidgetBoundary></Panel>
          </Section>
          <Section title="Busiest tools">
            <Panel className="px-4 py-2">
              <WidgetBoundary title="Tools">
                {!q.data ? <Skeleton className="h-40 w-full" /> : q.data.tools.by_tool.length === 0 ? <p className="py-6 text-small text-muted">No tool calls yet.</p> : (
                  <table className="w-full text-small">
                    <caption className="sr-only">Tool usage</caption>
                    <thead><tr className="text-left text-meta text-muted"><th className="py-1.5 font-normal">Tool</th><th className="font-normal">Calls</th><th className="font-normal">Failed</th><th className="text-right font-normal">p95</th></tr></thead>
                    <tbody>{q.data.tools.by_tool.slice(0, 8).map((r) => (
                      <tr key={r.tool} className="border-t border-border"><td className="py-1.5"><Link href={`/tools/${r.tool}`} className="font-mono text-[12.5px] hover:underline">{r.tool}</Link></td>
                        <td className="tabular">{r.calls}</td><td className={r.failures ? "tabular text-danger" : "tabular text-muted"}>{r.failures}</td><td className="tabular text-right font-mono text-[12.5px]">{formatMs(r.p95_ms)}</td></tr>
                    ))}</tbody>
                  </table>
                )}
              </WidgetBoundary>
            </Panel>
          </Section>
        </div>
      </div>
    </PageContainer>
  );
}

const WINDOWS = [{ h: 24, label: "Today" }, { h: 24 * 7, label: "7 days" }, { h: 24 * 30, label: "30 days" }];

export function MonitoringView() {
  const [hours, setHours] = useState(24);
  const q = useSummary(hours);
  const ready = useQuery({ queryKey: ["ready"], queryFn: apiClient.monitoring.ready, refetchInterval: 15_000 });
  const s = q.data;
  return (
    <PageContainer>
      <PageHeader title="Monitoring" description="Latency, errors and outcomes across chat and voice."
        actions={<div role="group" aria-label="Time window" className="flex rounded-control border border-border-strong p-0.5">
          {WINDOWS.map((w) => <button key={w.h} aria-pressed={hours === w.h} onClick={() => setHours(w.h)} className={`rounded-[4px] px-2.5 py-1 text-small ${hours === w.h ? "bg-raised text-strong" : "text-muted hover:text-foreground"}`}>{w.label}</button>)}
        </div>} />
      <div className="grid gap-8">
        <Section title="System health">
          <Panel className="flex flex-wrap gap-x-8 gap-y-2 px-4 py-3 text-small">
            {ready.data ? Object.entries(ready.data.checks).map(([k, ok]) => <span key={k} className="inline-flex items-center gap-2"><span aria-hidden className={`size-1.5 rounded-full ${ok ? "bg-success" : "bg-danger"}`} />{titleCase(k)} <span className="text-muted">{ok ? "operational" : "unavailable"}</span></span>)
              : ready.error ? <span className="text-danger">Backend unreachable</span> : <Skeleton className="h-4 w-80" />}
          </Panel>
        </Section>
        {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !s ? <Skeleton className="h-64" /> : <>
          <KpiStrip s={s} />
          <Section title="Tool calls and latency" description="Average latency of completed calls per interval.">
            <Panel className="h-64 px-2 py-3">
              <WidgetBoundary title="Latency chart">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={s.series.map((p) => ({ ...p, t: new Date(p.t).toLocaleString("en-IN", hours > 24 ? { day: "2-digit", month: "short" } : { hour: "2-digit", minute: "2-digit" }) }))}>
                    <CartesianGrid stroke="#1a1a1a" vertical={false} />
                    <XAxis dataKey="t" stroke="#777" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={24} />
                    <YAxis yAxisId="l" stroke="#777" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={44} unit="ms" />
                    <YAxis yAxisId="c" orientation="right" stroke="#777" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={32} />
                    <RTooltip contentStyle={{ background: "#111", border: "1px solid #2a2a2a", borderRadius: 6, fontSize: 12 }} labelStyle={{ color: "#e5e5e5" }} />
                    <Line yAxisId="c" type="monotone" dataKey="calls" name="Calls" stroke="#8a8a8a" strokeWidth={1.25} dot={false} isAnimationActive={false} />
                    <Line yAxisId="c" type="monotone" dataKey="failures" name="Failures" stroke="#e5534b" strokeWidth={1.25} dot={false} isAnimationActive={false} />
                    <Line yAxisId="l" type="monotone" dataKey="latency_ms" name="Latency" stroke="#ffffff" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </WidgetBoundary>
            </Panel>
          </Section>
          <div className="grid gap-8 lg:grid-cols-2">
            <Section title="Voice media">
              <Panel className="px-4 py-4 text-small">
                {s.voice_media ? null : <>
                  <p className="text-foreground">STT, TTS, time-to-first-audio and interruption rates are exported by the voice worker as Prometheus metrics.</p>
                  <p className="mt-1 text-muted">They aren&apos;t stored per conversation, so they&apos;re not estimated here. Scrape the worker or connect your metrics stack.</p>
                  <ul className="mt-3 grid gap-1 font-mono text-meta text-muted">
                    {["bfsi_voice_stt_latency_seconds", "bfsi_voice_tts_ttfb_seconds", "bfsi_voice_time_to_first_audio_seconds", "bfsi_voice_interruptions_total", "bfsi_voice_call_duration_seconds"].map((m) => <li key={m}>{m}</li>)}
                  </ul>
                </>}
              </Panel>
            </Section>
            <Section title="Outcomes">
              <Panel className="grid gap-4 px-4 py-4 text-small">
                <div><p className="mb-1.5 text-meta text-muted">Policy decisions</p>
                  <div className="flex flex-wrap gap-1.5">{Object.entries(s.policy_decisions).map(([k, v]) => <Badge key={k} tone={k === "ALLOW" ? "success" : k === "DENY" ? "danger" : "warning"}>{titleCase(k)} · {v}</Badge>)}{!Object.keys(s.policy_decisions).length && <span className="text-muted">None yet</span>}</div></div>
                <div><p className="mb-1.5 text-meta text-muted">Top intents</p>
                  <div className="flex flex-wrap gap-1.5">{Object.entries(s.intents).map(([k, v]) => <Badge key={k}>{titleCase(k)} · {v}</Badge>)}{!Object.keys(s.intents).length && <span className="text-muted">None yet</span>}</div></div>
                <div><p className="mb-1.5 text-meta text-muted">Handoff reasons</p>
                  <div className="flex flex-wrap gap-1.5">{Object.entries(s.handoffs.by_reason).map(([k, v]) => <Badge key={k} tone="info">{titleCase(k)} · {v}</Badge>)}{!Object.keys(s.handoffs.by_reason).length && <span className="text-muted">None</span>}</div></div>
              </Panel>
            </Section>
          </div>
        </>}
      </div>
    </PageContainer>
  );
}
