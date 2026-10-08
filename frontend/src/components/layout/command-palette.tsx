"use client";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { ArrowUpRight, AudioLines, FileSearch, FlaskConical, MessageSquareText, ScrollText, Search, TriangleAlert, User, Wrench } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import * as D from "@radix-ui/react-dialog";
import { flatNav } from "@/config/navigation";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { useUi } from "@/store/ui";

const ACTIONS = [
  { label: "Start a chat session", href: "/chat", icon: MessageSquareText, kw: "talk agent test" },
  { label: "Start a voice session", href: "/voice", icon: AudioLines, kw: "call phone" },
  { label: "Find customer", href: "/customers", icon: User, kw: "search people" },
  { label: "Search knowledge", href: "/knowledge/search", icon: FileSearch, kw: "rag documents policy" },
  { label: "Test an MCP tool", href: "/mcp", icon: Wrench, kw: "tool test" },
  { label: "View failed tool calls", href: "/audit?outcome=failure", icon: TriangleAlert, kw: "errors failures transactions" },
  { label: "Open audit logs", href: "/audit", icon: ScrollText, kw: "events logs" },
  { label: "Run evaluation suite", href: "/evaluation", icon: FlaskConical, kw: "tests scenarios" },
];

export function CommandPalette() {
  const { paletteOpen: open, setPalette } = useUi();
  const router = useRouter();
  const { can } = useProfile();
  const t = useTenantKey();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(!useUi.getState().paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette]);

  const enabled = open;
  const tools = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list, enabled: enabled && can("agent.read") });
  const docs = useQuery({ queryKey: [t, "documents"], queryFn: apiClient.documents.list, enabled: enabled && can("knowledge.read") });
  const convs = useQuery({ queryKey: [t, "conversations"], queryFn: () => apiClient.conversations.list(100), enabled: enabled && can("conversation.read") });
  const integ = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list, enabled: enabled && can("integration.manage") });
  const mcp = useQuery({ queryKey: [t, "mcp"], queryFn: apiClient.mcp.list, enabled: enabled && can("mcp.manage") });
  const pols = useQuery({ queryKey: [t, "policies"], queryFn: apiClient.policies.list, enabled: enabled && can("policy.manage") });
  const customers = useQuery({ queryKey: [t, "customers", ""], queryFn: () => apiClient.customers.list(""), enabled: enabled && can("customer.read") });
  const audit = useQuery({ queryKey: [t, "audit", "palette"], queryFn: () => apiClient.audit.events({ limit: 50 }), enabled: enabled && can("audit.read") });

  const go = (href: string) => { setPalette(false); router.push(href); };
  const item = "flex cursor-default items-center gap-2.5 rounded-[5px] px-2.5 py-2 text-small text-foreground aria-selected:bg-hover [&_svg]:size-4 [&_svg]:text-subtle";
  const group = "px-1 [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-meta [&_[cmdk-group-heading]]:text-subtle";

  return (
    <D.Root open={open} onOpenChange={setPalette}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[var(--z-palette)] bg-black/70" />
        <D.Content aria-label="Command palette" className="fixed left-1/2 top-[12vh] z-[var(--z-palette)] w-[calc(100%-32px)] max-w-xl -translate-x-1/2 overflow-hidden rounded-panel border border-border-strong bg-surface shadow-2xl shadow-black/70">
          <D.Title className="sr-only">Search</D.Title>
          <Command label="Search anything" loop>
            <div className="flex items-center gap-2 border-b border-border px-3">
              <Search aria-hidden className="size-4 text-subtle" />
              <Command.Input autoFocus placeholder="Search anything…" className="h-11 flex-1 bg-transparent text-body text-foreground placeholder:text-subtle focus:outline-none" />
            </div>
            <Command.List className="max-h-[60vh] overflow-y-auto pb-2">
              <Command.Empty className="px-4 py-6 text-small text-muted">No matches. Try a customer name, a tool like block_card, or a page.</Command.Empty>
              <Command.Group heading="Actions" className={group}>
                {ACTIONS.map((a) => <Command.Item key={a.href + a.label} value={`${a.label} ${a.kw}`} onSelect={() => go(a.href)} className={item}><a.icon />{a.label}</Command.Item>)}
              </Command.Group>
              {customers.data?.length ? <Command.Group heading="Customers" className={group}>
                {customers.data.map((c) => <Command.Item key={c.id} value={`customer ${c.name} ${c.id}`} onSelect={() => go(`/customers/${c.id}`)} className={item}><User />{c.name}<span className="ml-auto font-mono text-meta text-muted">{c.id}</span></Command.Item>)}
              </Command.Group> : null}
              {convs.data?.length ? <Command.Group heading="Conversations" className={group}>
                {convs.data.slice(0, 20).map((c) => <Command.Item key={c.id} value={`conversation ${c.id} ${c.customer_ref ?? ""} ${c.last_intent ?? ""} ${c.channel}`} onSelect={() => go(`/conversations/${c.id}`)} className={item}><MessageSquareText />{c.customer_ref ?? "Anonymous"} · {c.channels_used.join(" + ")}<span className="ml-auto font-mono text-meta text-muted">{c.id.slice(0, 8)}</span></Command.Item>)}
              </Command.Group> : null}
              {tools.data?.length ? <Command.Group heading="Tools" className={group}>
                {tools.data.map((x) => <Command.Item key={x.name} value={`tool ${x.name} ${x.source}`} onSelect={() => go(`/tools/${x.name}`)} className={item}><Wrench /><span className="font-mono text-[12.5px]">{x.name}</span><span className="ml-auto text-meta text-muted">{x.source}</span></Command.Item>)}
              </Command.Group> : null}
              {mcp.data?.length ? <Command.Group heading="MCP servers" className={group}>
                {mcp.data.map((x) => <Command.Item key={x.id} value={`mcp server ${x.name}`} onSelect={() => go(`/mcp/${x.id}`)} className={item}><ArrowUpRight />{x.name}</Command.Item>)}
              </Command.Group> : null}
              {docs.data?.length ? <Command.Group heading="Documents" className={group}>
                {docs.data.map((x) => <Command.Item key={x.id} value={`document ${x.title} ${x.product ?? ""}`} onSelect={() => go(`/knowledge/documents/${x.id}`)} className={item}><FileSearch />{x.title}</Command.Item>)}
              </Command.Group> : null}
              {integ.data?.length ? <Command.Group heading="Integrations" className={group}>
                {integ.data.map((x) => <Command.Item key={x.id} value={`integration ${x.name} ${x.kind}`} onSelect={() => go(`/integrations/${x.id}`)} className={item}><ArrowUpRight />{x.name}</Command.Item>)}
              </Command.Group> : null}
              {pols.data?.effective.length ? <Command.Group heading="Policies" className={group}>
                {pols.data.effective.map((x) => <Command.Item key={x.id} value={`policy ${x.name} ${x.effect}`} onSelect={() => go(`/policies/${encodeURIComponent(x.id)}`)} className={item}><ArrowUpRight />{x.name}</Command.Item>)}
              </Command.Group> : null}
              {audit.data?.length ? <Command.Group heading="Audit events" className={group}>
                {audit.data.slice(0, 15).map((x) => <Command.Item key={x.seq} value={`audit ${x.event_type} ${x.resource ?? ""} ${x.outcome}`} onSelect={() => go(`/audit?seq=${x.seq}`)} className={item}><ScrollText /><span className="font-mono text-[12.5px]">{x.event_type}</span><span className="ml-auto text-meta text-muted">#{x.seq}</span></Command.Item>)}
              </Command.Group> : null}
              <Command.Group heading="Go to" className={group}>
                {flatNav().filter((n) => !n.perm || can(n.perm)).map((n) => <Command.Item key={n.href} value={`go ${n.label}`} onSelect={() => go(n.href)} className={item}><n.icon />{n.label}</Command.Item>)}
              </Command.Group>
            </Command.List>
          </Command>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
