"use client";
import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { AgentMarkdown } from "@/components/chat/message";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader, Panel, Section } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { useProfile } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { titleCase } from "@/utils/format";

export function SearchLab() {
  const { profile } = useProfile();
  const [query, setQuery] = useState("What is the foreclosure charge for a home loan?");
  const [product, setProduct] = useState("any");
  const [internal, setInternal] = useState(false);
  const search = useMutation({ mutationFn: () => apiClient.knowledge.search(query, { top_k: 8, product: product === "any" ? undefined : product, include_internal: internal }) });
  // The answer comes from the real agent in a throwaway anonymous session (same path customers use).
  const answer = useMutation({ mutationFn: async () => {
    const s = await apiClient.sessions.create(profile?.tenant_slug || "demo-bank");
    return apiClient.chat.send(s.session.session_id, s.session_token, query);
  } });
  const run = () => { if (query.trim().length > 2) { search.mutate(); answer.mutate(); } };
  return (
    <PageContainer>
      <PageHeader title="Knowledge search" description="Test retrieval the way the agent sees it: tenant-filtered, in-date, hybrid search with reranking." />
      <form className="mb-6 grid gap-2 sm:grid-cols-[1fr_180px_auto]" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <Input aria-label="Question" value={query} onChange={(e) => setQuery(e.target.value)} className="h-10 text-lead" />
        <Select aria-label="Product filter" value={product} onValueChange={setProduct} className="h-10"
          options={[{ value: "any", label: "Any product" }, ...["home_loan", "credit_card", "savings_account", "personal_loan"].map((p) => ({ value: p, label: titleCase(p) }))]} />
        <Button type="submit" variant="primary" size="lg" loading={search.isPending}><Search />Search</Button>
        <label className="flex items-center gap-2 text-small text-muted sm:col-span-3"><Switch checked={internal} onCheckedChange={setInternal} aria-label="Include internal documents" />Include internal documents (staff view; customers never see these)</label>
      </form>
      <div className="grid gap-8 lg:grid-cols-[1fr_1fr]">
        <Section title="Answer" description="What the agent replies to a customer who isn't signed in.">
          <Panel className="p-4">
            {answer.isPending ? <Skeleton className="h-24" /> : answer.error ? <ErrorState compact error={answer.error} /> : answer.data ? (
              <><AgentMarkdown text={answer.data.text} />
                <p className="mt-3 text-meta text-muted">{answer.data.sources.length} source(s) cited · intent {titleCase(answer.data.intent ?? "—")}</p></>
            ) : <p className="text-small text-muted">Run a search to see the agent&apos;s answer.</p>}
          </Panel>
        </Section>
        <Section title="Sources" description="Ranked after fusion (BM25 + vectors) and reranking.">
          {search.isPending ? <Skeleton className="h-48" /> : search.error ? <ErrorState error={search.error} /> : !search.data ? <p className="text-small text-muted">Results appear here.</p>
            : search.data.results.length === 0 ? <p className="text-small text-muted">Nothing relevant passed the relevance threshold — the agent would say it couldn&apos;t find this and offer a person.</p> : (
            <ol className="grid gap-3">{search.data.results.map((r) => (
              <li key={r.chunk_id}><Panel className="p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-small font-medium text-foreground">{r.index}. {r.title} <span className="text-muted">v{r.version} · page {r.page}</span></p>
                  <Badge tone="neutral"><span className="font-mono">rerank {r.score.toFixed(3)}</span></Badge>
                </div>
                {r.section && <p className="mt-1 text-meta text-muted">{r.section}</p>}
                <p className="mt-2 text-small text-muted">{r.snippet.slice(0, 420)}{r.snippet.length > 420 ? "…" : ""}</p>
              </Panel></li>
            ))}</ol>
          )}
        </Section>
      </div>
    </PageContainer>
  );
}
