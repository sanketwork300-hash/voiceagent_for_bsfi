"use client";
import { useQuery } from "@tanstack/react-query";
import { PageContainer } from "@/components/layout/app-shell";
import { PageHeader, Panel, SampleDataNote } from "@/components/ui/page";
import { SkeletonRows } from "@/components/ui/states";
import { StatusDot } from "@/components/ui/status-dot";
import { apiClient } from "@/lib/api/client";

export function WorkflowsView() {
  const q = useQuery({ queryKey: ["workflows"], queryFn: apiClient.workflows.list });
  return (
    <PageContainer>
      <PageHeader title="Workflows" description="How multi-step requests unfold. These flows are enforced by the runtime and policy engine; they're shown here for review." meta={<SampleDataNote what="Workflow definitions" />} />
      {!q.data ? <SkeletonRows /> : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {q.data.map((w) => (
            <li key={w.id}><Panel className="grid gap-3 p-4">
              <div className="flex items-center justify-between"><p className="text-body font-medium text-strong">{w.name}</p><StatusDot tone="success" label={w.status} /></div>
              <p className="text-meta text-muted">Starts when: {w.trigger}</p>
              <ol className="grid gap-1.5">{w.steps.map((s, i) => (
                <li key={s} className="grid grid-cols-[22px_1fr] items-start gap-2 text-small"><span className="mt-px grid size-[18px] place-items-center rounded-full border border-border-strong font-mono text-[10px] text-muted">{i + 1}</span>{s}</li>
              ))}</ol>
            </Panel></li>
          ))}
        </ul>
      )}
    </PageContainer>
  );
}
