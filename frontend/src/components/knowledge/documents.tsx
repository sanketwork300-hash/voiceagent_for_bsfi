"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { KnowledgeDocument } from "@/types/domain";
import { formatDate, languageLabel, titleCase } from "@/utils/format";

const current = (d: KnowledgeDocument) => d.versions.find((v) => v.id === d.current_version_id) ?? d.versions[d.versions.length - 1];
const PRODUCTS = ["home_loan", "personal_loan", "credit_card", "debit_card", "savings_account", "fixed_deposit", "insurance"];
const TYPES = ["policy", "terms", "fees", "faq", "sop", "regulatory"];

export function UploadDialog({ open, onOpenChange, existing }: { open: boolean; onOpenChange: (o: boolean) => void; existing?: KnowledgeDocument }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({ title: "", product: "", doc_type: "", language: "en", access_level: "public", version: "", effective_from: "", effective_until: "" });
  const [error, setError] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.set("file", file!);
      for (const [k, v] of Object.entries(meta)) if (v) form.set(k, v);
      if (existing) form.set("document_id", existing.id);
      return apiClient.documents.upload(form);
    },
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: [t, "documents"] });
      if (r.status === "failed") setError(`Uploaded, but indexing failed: ${r.error ?? "unknown error"}`);
      else { onOpenChange(false); setFile(null); }
    },
    onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Upload failed."),
  });
  const set = (k: keyof typeof meta) => (v: string) => setMeta((x) => ({ ...x, [k]: v }));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={existing ? `New version of ${existing.title}` : "Upload document"} description="PDF, text, Markdown or HTML up to 25 MB. The new version becomes the one the agent uses once indexed.">
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); setError(null); if (file) m.mutate(); }}>
          <Field label="File" htmlFor="doc-file">
            <input id="doc-file" type="file" accept=".pdf,.txt,.md,.html,.htm" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-small text-muted file:mr-3 file:rounded-control file:border file:border-border-strong file:bg-raised file:px-3 file:py-1.5 file:text-small file:text-foreground" />
          </Field>
          {!existing && <Field label="Title" htmlFor="doc-title" hint="Defaults to the file name"><Input id="doc-title" value={meta.title} onChange={(e) => set("title")(e.target.value)} /></Field>}
          <div className="grid grid-cols-2 gap-3">
            {!existing && <Field label="Product" htmlFor="doc-product"><Select id="doc-product" value={meta.product} onValueChange={set("product")} placeholder="Detect automatically" options={PRODUCTS.map((p) => ({ value: p, label: titleCase(p) }))} /></Field>}
            {!existing && <Field label="Document type" htmlFor="doc-type"><Select id="doc-type" value={meta.doc_type} onValueChange={set("doc_type")} placeholder="Detect automatically" options={TYPES.map((p) => ({ value: p, label: titleCase(p) }))} /></Field>}
            {!existing && <Field label="Access" htmlFor="doc-access" hint="Internal documents are never shown to customers"><Select id="doc-access" value={meta.access_level} onValueChange={set("access_level")} options={[{ value: "public", label: "Public" }, { value: "customer", label: "Signed-in customers" }, { value: "internal", label: "Internal staff only" }]} /></Field>}
            <Field label="Version" htmlFor="doc-version"><Input id="doc-version" value={meta.version} onChange={(e) => set("version")(e.target.value)} placeholder="e.g. 4.2" /></Field>
            <Field label="Effective from" htmlFor="doc-from"><Input id="doc-from" type="date" value={meta.effective_from} onChange={(e) => set("effective_from")(e.target.value)} /></Field>
            <Field label="Effective until" htmlFor="doc-until"><Input id="doc-until" type="date" value={meta.effective_until} onChange={(e) => set("effective_until")(e.target.value)} /></Field>
          </div>
          {error && <p role="alert" className="text-small text-danger">{error}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!file} loading={m.isPending}><FileUp />Upload and index</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DocumentList() {
  const t = useTenantKey();
  const router = useRouter();
  const { can } = useProfile();
  const q = useQuery({ queryKey: [t, "documents"], queryFn: apiClient.documents.list });
  const [upload, setUpload] = useState(false);
  const [f, setF] = useState({ text: "", product: "all", type: "all", language: "all", status: "all", access: "all", from: "" });
  const rows = useMemo(() => (q.data ?? []).filter((d) => {
    const v = current(d);
    return (!f.text || d.title.toLowerCase().includes(f.text.toLowerCase())) && (f.product === "all" || d.product === f.product)
      && (f.type === "all" || d.doc_type === f.type) && (f.language === "all" || d.language === f.language)
      && (f.status === "all" || v?.status === f.status) && (f.access === "all" || d.access_level === f.access)
      && (!f.from || (v?.effective_from ?? "") >= f.from);
  }), [q.data, f]);
  const columns: Column<KnowledgeDocument>[] = [
    { key: "title", header: "Document", primary: true, cell: (d) => <span className="font-medium text-foreground">{d.title}</span>, sort: (d) => d.title },
    { key: "product", header: "Product", cell: (d) => (d.product ? titleCase(d.product) : <span className="text-muted">General</span>) },
    { key: "version", header: "Version", cell: (d) => <span className="font-mono text-[12.5px]">v{current(d)?.version ?? "—"}</span> },
    { key: "effective", header: "Effective from", cell: (d) => formatDate(current(d)?.effective_from) },
    { key: "access", header: "Access", cell: (d) => <Badge tone={d.access_level === "internal" ? "warning" : "neutral"}>{titleCase(d.access_level)}</Badge>, hideOnMobile: true },
    { key: "status", header: "Status", cell: (d) => { const v = current(d); return <StatusDot tone={v?.status === "indexed" ? "success" : healthTone(v?.status)} label={v?.status === "indexed" ? "Active" : titleCase(v?.status ?? "—")} />; } },
    { key: "updated", header: "Last updated", cell: (d) => <span className="text-muted">{formatDate(current(d)?.created_at)}</span>, sort: (d) => current(d)?.created_at ?? "" },
  ];
  const sel = (k: keyof typeof f, label: string, options: string[], fmt = titleCase) =>
    <Select aria-label={label} value={f[k]} onValueChange={(v) => setF((x) => ({ ...x, [k]: v }))} options={[{ value: "all", label: `Any ${label.toLowerCase()}` }, ...options.map((o) => ({ value: o, label: fmt(o) }))]} />;
  return (
    <PageContainer wide>
      <PageHeader title="Knowledge base" description="Approved documents the agent may answer from. Only active, in-date versions are used."
        actions={can("knowledge.manage") && <Button variant="primary" onClick={() => setUpload(true)}><FileUp />Upload document</Button>} />
      <div className="mb-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-7">
        <Input aria-label="Search documents" placeholder="Search documents…" value={f.text} onChange={(e) => setF((x) => ({ ...x, text: e.target.value }))} className="lg:col-span-2" />
        {sel("product", "Product", PRODUCTS)}{sel("type", "Document type", TYPES)}{sel("language", "Language", ["en", "hi", "mr", "ta", "te"], (x) => languageLabel(x))}
        {sel("status", "Status", ["indexed", "failed", "pending"], (x) => (x === "indexed" ? "Active" : titleCase(x)))}
        <Input aria-label="Effective from" type="date" value={f.from} onChange={(e) => setF((x) => ({ ...x, from: e.target.value }))} />
      </div>
      <DataTable caption="Documents" rows={q.data ? rows : undefined} error={q.error} onRetry={() => q.refetch()} columns={columns} getKey={(d) => d.id}
        onRowClick={(d) => router.push(`/knowledge/documents/${d.id}`)}
        empty={{ title: "No documents yet", description: "Upload product policies, fee schedules, FAQs and terms. The agent only answers from what's here.",
                 action: can("knowledge.manage") ? <Button onClick={() => setUpload(true)}><FileUp />Upload document</Button> : undefined }} />
      <UploadDialog open={upload} onOpenChange={setUpload} />
    </PageContainer>
  );
}

export function DocumentDetail({ id }: { id: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const { can } = useProfile();
  const docs = useQuery({ queryKey: [t, "documents"], queryFn: apiClient.documents.list });
  const d = docs.data?.find((x) => x.id === id);
  const uploads = useQuery({ queryKey: [t, "audit", "doc", id], queryFn: () => apiClient.audit.events({ event_type: "document.uploaded", limit: 200 }), enabled: can("audit.read") });
  const passages = useQuery({ queryKey: [t, "doc-passages", id], enabled: Boolean(d), queryFn: async () => (await apiClient.knowledge.search(d!.title, { top_k: 20, include_internal: true })).results.filter((r) => r.document_id === id) });
  const [newVersion, setNewVersion] = useState(false);
  const reindex = useMutation({ mutationFn: () => apiClient.documents.reindex(id), onSuccess: () => qc.invalidateQueries({ queryKey: [t, "documents"] }) });
  if (docs.error) return <PageContainer><ErrorState error={docs.error} /></PageContainer>;
  if (!docs.data) return <PageContainer><SkeletonRows /></PageContainer>;
  if (!d) return <PageContainer><EmptyState title="Document not found" description="It may have been removed, or it belongs to a different organization." action={<Link href="/knowledge/documents" className="text-small underline">Back to documents</Link>} /></PageContainer>;
  const v = current(d);
  const uploaderFor = (versionLabel: string) => uploads.data?.find((e) => e.resource === id && (e.payload as { version?: string }).version === versionLabel);
  return (
    <PageContainer>
      <PageHeader title={d.title} description={`${titleCase(d.doc_type)}${d.product ? ` · ${titleCase(d.product)}` : ""} · ${languageLabel(d.language)}`}
        meta={<StatusDot tone={v?.status === "indexed" ? "success" : healthTone(v?.status)} label={v?.status === "indexed" ? "Active" : titleCase(v?.status ?? "")} />}
        actions={can("knowledge.manage") && <>
          <Button onClick={() => setNewVersion(true)}><FileUp />Upload new version</Button>
          <ConfirmDialog tone="primary" trigger={<Button variant="ghost"><RefreshCw />Reindex</Button>} title="Reindex this document?"
            description="The current version is re-chunked and re-embedded. Answers keep using the existing index until it finishes." confirmLabel="Reindex" onConfirm={() => reindex.mutateAsync()} />
        </>} />
      <div className="grid gap-8 lg:grid-cols-[1fr_320px]">
        <div className="grid content-start gap-8">
          <Section title="Indexed passages" description="What the agent can retrieve from the current version.">
            {passages.isLoading ? <SkeletonRows /> : !passages.data?.length ? <p className="text-small text-muted">No passages retrieved for this document&apos;s title. Try the knowledge search lab with a specific question.</p> : (
              <ol className="grid gap-3">{passages.data.map((p) => (
                <li key={p.chunk_id}><Panel className="p-4"><p className="mb-1 text-meta text-muted">{p.section ?? "—"} · page {p.page ?? "—"} · <span className="font-mono">{p.chunk_id.split(":")[1] ? `chunk ${p.chunk_id.split(":")[1]}` : p.chunk_id}</span></p><p className="text-small text-foreground">{p.snippet}</p></Panel></li>
              ))}</ol>
            )}
          </Section>
          <Section title="Version history">
            <DataTable caption="Versions" rows={[...d.versions].reverse()} getKey={(x) => x.id} empty={{ title: "No versions", description: "" }} columns={[
              { key: "v", header: "Version", primary: true, cell: (x) => <span className="font-mono text-[12.5px]">v{x.version}</span> },
              { key: "s", header: "Status", cell: (x) => <StatusDot tone={x.status === "indexed" ? "success" : healthTone(x.status)} label={x.status === "indexed" ? "Active" : titleCase(x.status)} /> },
              { key: "c", header: "Chunks", cell: (x) => <span className="tabular">{x.chunks}</span> },
              { key: "e", header: "Effective", cell: (x) => `${formatDate(x.effective_from)} – ${x.effective_until ? formatDate(x.effective_until) : "open"}` },
              { key: "u", header: "Uploaded", cell: (x) => <span className="text-muted">{formatDate(x.created_at)}{uploaderFor(x.version)?.actor_id ? ` · staff ${uploaderFor(x.version)!.actor_id!.slice(0, 6)}` : ""}</span> },
            ]} />
          </Section>
        </div>
        <aside className="grid content-start gap-6">
          <Section title="Document information">
            <Panel className="p-4"><KeyValue items={[
              { label: "File", value: v?.filename ?? "—" }, { label: "Version", value: `v${v?.version ?? "—"}`, mono: true },
              { label: "Effective", value: `${formatDate(v?.effective_from)} – ${v?.effective_until ? formatDate(v.effective_until) : "open"}` },
              { label: "Access", value: d.access_level === "internal" ? "Internal staff only" : d.access_level === "customer" ? "Signed-in customers" : "Public" },
              { label: "Chunks", value: v?.chunks ?? 0 }, { label: "Index", value: "Hybrid: BM25 + dense vectors, reranked" },
              ...(v?.error ? [{ label: "Error", value: <span className="text-danger">{v.error}</span> }] : []),
            ]} /></Panel>
          </Section>
        </aside>
      </div>
      <UploadDialog open={newVersion} onOpenChange={setNewVersion} existing={d} />
    </PageContainer>
  );
}
