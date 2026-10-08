"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RiskBadge } from "@/components/ui/domain-badges";
import { Field, Input, Textarea } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot } from "@/components/ui/status-dot";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Agent } from "@/types/domain";
import { LANGUAGE_NAMES, titleCase } from "@/utils/format";

export function AgentList() {
  const t = useTenantKey();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useProfile();
  const q = useQuery({ queryKey: [t, "agents"], queryFn: apiClient.agents.list });
  const create = useMutation({ mutationFn: () => apiClient.agents.create({ name: "New agent", channels: ["chat", "voice"], languages: ["en", "hi"] }),
    onSuccess: (a) => { void qc.invalidateQueries({ queryKey: [t, "agents"] }); router.push(`/agents/${a.id}`); } });
  return (
    <PageContainer>
      <PageHeader title="Agents" description="Assistant personas. Every agent runs on the same runtime, tools and policies for chat and voice."
        actions={can("agent.manage") && <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}><Plus />New agent</Button>} />
      {q.error ? <ErrorState error={q.error} /> : !q.data ? <SkeletonRows /> : q.data.length === 0 ? <EmptyState icon={<Bot />} title="No agents" description="Create an agent to start serving customers." /> : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {q.data.map((a) => (
            <li key={a.id}><Link href={`/agents/${a.id}`} className="grid gap-2 rounded-panel border border-border bg-surface p-4 hover:border-border-strong hover:bg-hover">
              <div className="flex items-center justify-between"><p className="text-body font-medium text-strong">{a.name}</p><StatusDot tone={a.is_active ? "success" : "neutral"} label={a.is_active ? "Online" : "Paused"} /></div>
              <p className="line-clamp-2 text-small text-muted">{a.description || "No description."}</p>
              <div className="flex flex-wrap gap-1">{a.channels.map((c) => <Badge key={c} tone={c === "voice" ? "voice" : "neutral"}>{c}</Badge>)}<Badge>{a.languages.length} languages</Badge>
                <Badge>{a.allowed_tools ? `${a.allowed_tools.length} tools` : "All tools"}</Badge></div>
            </Link></li>
          ))}
        </ul>
      )}
    </PageContainer>
  );
}

const STT = [{ value: "deepgram", label: "Deepgram (multilingual, Hinglish)" }, { value: "sarvam", label: "Sarvam (Indic)" }, { value: "openai", label: "OpenAI" }];
const TTS = [{ value: "sarvam", label: "Sarvam (Indic voices)" }, { value: "elevenlabs", label: "ElevenLabs" }, { value: "openai", label: "OpenAI" }];

export function AgentBuilder({ id }: { id: string }) {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "agent", id], queryFn: () => apiClient.agents.get(id) });
  if (q.error) return <PageContainer><ErrorState error={q.error} /></PageContainer>;
  if (!q.data) return <PageContainer><SkeletonRows /></PageContainer>;
  // remount the form when the stored agent changes, so the draft starts from the saved version
  return <AgentForm key={JSON.stringify(q.data)} agent={q.data} />;
}

function AgentForm({ agent }: { agent: Agent }) {
  const id = agent.id;
  const t = useTenantKey();
  const qc = useQueryClient();
  const { can } = useProfile();
  const tools = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list });
  const docs = useQuery({ queryKey: [t, "documents"], queryFn: apiClient.documents.list, enabled: can("knowledge.read") });
  const pols = useQuery({ queryKey: [t, "policies"], queryFn: apiClient.policies.list, enabled: can("policy.manage") });
  const [draft, setDraft] = useState<Agent>(agent);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (a: Agent) => apiClient.agents.update(id, { name: a.name, description: a.description, persona_prompt: a.persona_prompt, channels: a.channels, languages: a.languages,
      voice_config: a.voice_config, llm_config: a.llm_config ?? {}, is_active: a.is_active, ...(a.allowed_tools === null ? { clear_tool_restriction: true } : { allowed_tools: a.allowed_tools }) }),
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000); void qc.invalidateQueries({ queryKey: [t, "agent", id] }); void qc.invalidateQueries({ queryKey: [t, "agents"] }); },
    onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Not saved."),
  });
  const editable = can("agent.manage");
  const set = <K extends keyof Agent>(k: K, v: Agent[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const vc = draft.voice_config as { stt_provider?: string; tts_provider?: string; voices?: Record<string, string> };
  const userTools = (tools.data ?? []).filter((x) => !x.internal && x.source !== "builtin");
  const restricted = draft.allowed_tools !== null;
  const toggleList = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <PageContainer>
      <PageHeader title={draft.name} description="Configure the persona and what it may do. Governance (risk, authentication, confirmation) stays with tools and policies."
        meta={<StatusDot tone={draft.is_active ? "success" : "neutral"} label={draft.is_active ? "Online" : "Paused"} />}
        actions={editable && <>{saved && <span role="status" className="self-center text-small text-success">Saved</span>}<Button variant="primary" loading={save.isPending} onClick={() => save.mutate(draft)}>Save agent</Button></>} />
      {error && <p role="alert" className="mb-4 text-small text-danger">{error}</p>}
      <Tabs defaultValue="general">
        <TabsList>{["general", "model", "knowledge", "tools", "policies", "voice", "security", "advanced"].map((v) => <TabsTrigger key={v} value={v}>{titleCase(v)}</TabsTrigger>)}</TabsList>
        <fieldset disabled={!editable} className="contents">
          <TabsContent value="general" className="grid max-w-2xl gap-4">
            <Field label="Agent name" htmlFor="a-name"><Input id="a-name" value={draft.name} onChange={(e) => set("name", e.target.value)} /></Field>
            <Field label="Description" htmlFor="a-desc"><Textarea id="a-desc" value={draft.description} onChange={(e) => set("description", e.target.value)} /></Field>
            <Field label="System instructions" htmlFor="a-persona" hint="Tone and focus. Safety rules (never invent numbers, cite sources, no secrets) are always added by the platform and can't be removed.">
              <Textarea id="a-persona" value={draft.persona_prompt} onChange={(e) => set("persona_prompt", e.target.value)} className="min-h-28" /></Field>
            <div className="grid gap-2"><p className="text-small font-medium">Channels</p>
              <div className="flex gap-4">{(["chat", "voice"] as const).map((c) => <label key={c} className="flex items-center gap-2 text-small"><Switch checked={draft.channels.includes(c)} onCheckedChange={() => set("channels", toggleList(draft.channels, c) as Agent["channels"])} aria-label={`Enable ${c}`} />{titleCase(c)}</label>)}</div></div>
            <div className="grid gap-2"><p className="text-small font-medium">Supported languages</p><p className="text-meta text-muted">Detected automatically each turn — customers never have to pick.</p>
              <div className="flex flex-wrap gap-1.5">{Object.entries(LANGUAGE_NAMES).map(([code, n]) => (
                <button type="button" key={code} aria-pressed={draft.languages.includes(code)} onClick={() => set("languages", toggleList(draft.languages, code))}
                  className={`rounded-full border px-3 py-1 text-small ${draft.languages.includes(code) ? "border-strong text-strong" : "border-border-strong text-muted"}`}>{n.native}</button>
              ))}</div></div>
            <label className="flex items-center gap-2 text-small"><Switch checked={draft.is_active} onCheckedChange={(v) => set("is_active", v)} aria-label="Agent online" />Online</label>
          </TabsContent>
          <TabsContent value="model" className="grid max-w-2xl gap-4">
            <Panel className="p-4 text-small text-muted">The LLM provider is configured per deployment (OpenAI-compatible, self-hosted, or the offline engine). Values here are stored on the agent for per-agent routing; this deployment uses the platform default for all agents.</Panel>
            <Field label="Provider" htmlFor="m-provider"><Select id="m-provider" value={String(draft.llm_config?.provider ?? "default")} onValueChange={(v) => set("llm_config", { ...draft.llm_config, provider: v })}
              options={[{ value: "default", label: "Platform default" }, { value: "openai", label: "OpenAI-compatible" }, { value: "local", label: "Self-hosted model" }, { value: "other", label: "Other provider" }]} /></Field>
            <Field label="Model" htmlFor="m-model"><Input id="m-model" value={String(draft.llm_config?.model ?? "")} placeholder="Platform default" onChange={(e) => set("llm_config", { ...draft.llm_config, model: e.target.value })} /></Field>
            <Field label={`Temperature · ${Number(draft.llm_config?.temperature ?? 0.1).toFixed(1)}`} htmlFor="m-temp" hint="Low values keep banking answers consistent.">
              <input id="m-temp" type="range" min={0} max={1} step={0.1} value={Number(draft.llm_config?.temperature ?? 0.1)} onChange={(e) => set("llm_config", { ...draft.llm_config, temperature: Number(e.target.value) })} className="accent-white" /></Field>
          </TabsContent>
          <TabsContent value="knowledge" className="grid gap-3">
            <p className="text-small text-muted">Agents answer from your organization&apos;s active, in-date documents. Customers never receive internal documents.</p>
            <ul className="divide-y divide-border rounded-panel border border-border">{(docs.data ?? []).map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-small"><Link href={`/knowledge/documents/${d.id}`} className="hover:underline">{d.title}</Link><Badge tone={d.access_level === "internal" ? "warning" : "neutral"}>{titleCase(d.access_level)}</Badge></li>
            ))}{docs.data?.length === 0 && <li className="p-4 text-small text-muted">No documents yet.</li>}</ul>
          </TabsContent>
          <TabsContent value="tools" className="grid gap-3">
            <label className="flex items-center gap-2 text-small"><Switch checked={restricted} onCheckedChange={(v) => set("allowed_tools", v ? userTools.filter((x) => x.enabled).map((x) => x.name) : null)} aria-label="Restrict tools" />Only allow selected tools</label>
            <p className="text-meta text-muted">{restricted ? "The agent can request only the tools ticked below." : "The agent can request any enabled tool. The policy engine still gates every call."} Knowledge search and human handoff are always available.</p>
            <ul className="divide-y divide-border rounded-panel border border-border">{userTools.map((x) => (
              <li key={x.name} className="flex items-center gap-3 px-4 py-2.5">
                <input type="checkbox" className="accent-white" aria-label={`Allow ${x.name}`} disabled={!restricted} checked={!restricted || draft.allowed_tools!.includes(x.name)} onChange={() => set("allowed_tools", toggleList(draft.allowed_tools ?? [], x.name))} />
                <span className="flex-1 font-mono text-[12.5px]">{x.name}</span><RiskBadge level={x.risk_level} />{!x.enabled && <Badge>Disabled</Badge>}
              </li>
            ))}</ul>
          </TabsContent>
          <TabsContent value="policies" className="grid gap-3">
            <p className="text-small text-muted">Policies apply to every agent in the organization. Edit them in <Link href="/policies" className="underline">Policies</Link>.</p>
            <ul className="divide-y divide-border rounded-panel border border-border">{(pols.data?.effective ?? []).map((r) => <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-small"><span>{r.name}</span><Badge>{titleCase(r.effect)}</Badge></li>)}</ul>
          </TabsContent>
          <TabsContent value="voice" className="grid max-w-2xl gap-4">
            <Field label="Speech-to-text" htmlFor="v-stt"><Select id="v-stt" value={vc.stt_provider ?? "deepgram"} onValueChange={(v) => set("voice_config", { ...vc, stt_provider: v })} options={STT} /></Field>
            <Field label="Text-to-speech" htmlFor="v-tts"><Select id="v-tts" value={vc.tts_provider ?? "sarvam"} onValueChange={(v) => set("voice_config", { ...vc, tts_provider: v })} options={TTS} /></Field>
            <div className="grid gap-2"><p className="text-small font-medium">Voice per language</p>
              {draft.languages.map((l) => <Field key={l} label={LANGUAGE_NAMES[l]?.english ?? l} htmlFor={`v-${l}`}><Input id={`v-${l}`} placeholder="Provider default" value={vc.voices?.[l] ?? ""} onChange={(e) => set("voice_config", { ...vc, voices: { ...vc.voices, [l]: e.target.value } })} /></Field>)}</div>
            <p className="text-meta text-muted">Applied by the voice worker at the start of each call.</p>
          </TabsContent>
          <TabsContent value="security" className="grid max-w-2xl gap-3">
            <Panel className="p-4"><KeyValue items={[
              { label: "Account data", value: "Requires a verified session (bank login or OTP)" },
              { label: "Card blocking", value: "Verified + customer confirmation; relaxed to partial verification during fraud reports" },
              { label: "Transfers", value: "Transaction OTP bound to amount and payee, confirmation, maker-checker above ₹5,00,000" },
              { label: "Voice biometrics", value: "Never sufficient on its own for high-risk actions" },
              { label: "Secrets", value: "OTP, PIN, CVV and passwords are never stored or sent to the model" },
            ]} /></Panel>
            <p className="text-meta text-muted">Authentication requirements come from tool governance and policies, so they&apos;re identical for every agent and channel.</p>
          </TabsContent>
          <TabsContent value="advanced" className="grid gap-3">
            <p className="text-small text-muted">Raw configuration. Changes here apply when you save.</p>
            <pre className="overflow-x-auto rounded-control border border-border bg-background p-3 font-mono text-[12px] text-muted">{JSON.stringify(draft, null, 2)}</pre>
          </TabsContent>
        </fieldset>
      </Tabs>
    </PageContainer>
  );
}
