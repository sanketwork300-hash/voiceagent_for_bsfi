"use client";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, FlaskConical, TriangleAlert, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Metric, MetricStrip } from "@/components/ui/metrics";
import { PageHeader, Panel, Section } from "@/components/ui/page";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { apiClient } from "@/lib/api/client";
import type { Channel, EvaluationReport } from "@/types/domain";
import { formatMs, formatPercent, titleCase } from "@/utils/format";

const DIMENSIONS: [string, string][] = [["intent", "Intent accuracy"], ["tool_selection", "Tool accuracy"], ["authorization", "Authorization correctness"], ["grounding", "Groundedness"],
  ["response", "Response correctness"], ["safety", "No data leakage"], ["handoff", "Handoff correctness"], ["flow", "Flow (OTP / confirmation)"], ["speech_rendering", "Speech rendering"]];

export function EvaluationView() {
  const [channels, setChannels] = useState<Channel[]>(["chat", "voice"]);
  const run = useMutation({ mutationFn: () => apiClient.evaluation.run({ channels }) });
  const r = run.data;
  const scenarios = useMemo(() => groupScenarios(r), [r]);
  return (
    <PageContainer wide>
      <PageHeader title="Evaluation" description="The same scenario suite runs against chat and voice: intent, tool choice, authorization, grounding and security cases."
        actions={<>
          <div role="group" aria-label="Channels" className="flex rounded-control border border-border-strong p-0.5">
            {(["chat", "voice"] as Channel[]).map((c) => <button key={c} aria-pressed={channels.includes(c)} onClick={() => setChannels((x) => (x.includes(c) ? (x.length > 1 ? x.filter((y) => y !== c) : x) : [...x, c]))}
              className={`rounded-[4px] px-2.5 py-1 text-small ${channels.includes(c) ? "bg-raised text-strong" : "text-muted"}`}>{titleCase(c)}</button>)}
          </div>
          <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}><FlaskConical />Run suite</Button>
        </>} />
      {run.error ? <ErrorState error={run.error} title="The evaluation didn't run" /> : !r ? (
        <EmptyState icon={<FlaskConical />} title={run.isPending ? "Running scenarios…" : "No run yet"}
          description={run.isPending ? "Each scenario opens a fresh session on each channel. This takes a few seconds." : "Run the suite after changing models, prompts, tools or policies — it's the regression gate."} />
      ) : (
        <div className="grid gap-8">
          {Object.entries(r.metrics).map(([ch, m]) => (
            <Section key={ch} title={titleCase(ch)}>
              <MetricStrip>
                <Metric label="Scenario pass rate" value={formatPercent(m.scenario_pass_rate)} sub={`${m.scenarios} scenarios · ${m.turns} turns`} tone={m.scenario_pass_rate < 1 ? "warning" : undefined} />
                {DIMENSIONS.filter(([k]) => m.accuracy[k] !== undefined).slice(0, 6).map(([k, l]) => <Metric key={k} label={l} value={formatPercent(m.accuracy[k])} tone={m.accuracy[k] < 1 ? "warning" : undefined} />)}
                <Metric label="Latency p50 / p95" value={formatMs(m.latency_ms.p50)} sub={`p95 ${formatMs(m.latency_ms.p95)}`} />
              </MetricStrip>
            </Section>
          ))}
          <Section title="Scenarios">
            <Panel className="divide-y divide-border">
              {scenarios.map((s) => (
                <details key={s.key} className="group px-4 py-3">
                  <summary className="flex cursor-pointer list-none items-center gap-3">
                    {s.status === "pass" ? <CheckCircle2 aria-label="Passed" className="size-4 text-success" /> : s.status === "review" ? <TriangleAlert aria-label="Needs review" className="size-4 text-warning" /> : <XCircle aria-label="Failed" className="size-4 text-danger" />}
                    <span className="flex-1 text-small text-foreground">{titleCase(s.key)}</span>
                    {s.channels.map((c) => <Badge key={c.channel} tone={c.passed ? "success" : "danger"}>{c.channel}</Badge>)}
                    <span className="text-meta text-muted">{s.status === "pass" ? "Passed" : s.status === "review" ? "Needs review" : "Failed"}</span>
                  </summary>
                  <ul className="mt-2 grid gap-1 pl-7 text-meta text-muted">
                    {s.turns.map((t) => <li key={`${t.channel}-${t.turn}`}>{t.channel} · turn {t.turn + 1}: {t.passed ? "ok" : t.failures.join("; ")} — <span className="text-subtle">{t.response.slice(0, 120)}</span></li>)}
                  </ul>
                </details>
              ))}
            </Panel>
          </Section>
        </div>
      )}
    </PageContainer>
  );
}

function groupScenarios(r?: EvaluationReport) {
  if (!r) return [];
  const map = new Map<string, EvaluationReport["results"]>();
  for (const x of r.results) map.set(x.scenario, [...(map.get(x.scenario) ?? []), x]);
  return [...map.entries()].map(([key, turns]) => {
    const channels = [...new Set(turns.map((t) => t.channel))].map((channel) => ({ channel, passed: turns.filter((t) => t.channel === channel).every((t) => t.passed) }));
    const passCount = channels.filter((c) => c.passed).length;
    return { key, turns, channels, status: passCount === channels.length ? "pass" : passCount > 0 ? "review" : "fail" };
  });
}
