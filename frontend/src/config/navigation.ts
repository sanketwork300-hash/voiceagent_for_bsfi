import {
  Activity, AudioLines, Bot, CheckSquare, ClipboardList, FileSearch, FileText, Fingerprint, Gauge,
  Headset, History, KeyRound, LayoutGrid, MessageSquareText, Network, PlugZap, ScrollText, Settings, ShieldCheck,
  Users, UserCog, Workflow, Wrench, Building2, Briefcase, FlaskConical, type LucideIcon,
} from "lucide-react";
import type { Permission } from "@/lib/permissions";

export interface NavItem { href: string; label: string; icon: LucideIcon; perm?: Permission; keywords?: string[] }
export interface NavGroup { label: string; items: NavItem[] }

export const NAV: (NavItem | NavGroup)[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutGrid },
  { label: "AI agent", items: [
    { href: "/chat", label: "Chat", icon: MessageSquareText },
    { href: "/voice", label: "Voice", icon: AudioLines },
    { href: "/sessions", label: "Sessions", icon: Activity, perm: "conversation.read" },
    { href: "/conversations", label: "Conversations", icon: History, perm: "conversation.read" },
  ] },
  { label: "Customers", items: [
    { href: "/customers", label: "Customers", icon: Users, perm: "customer.read" },
    { href: "/customers/authentication", label: "Authentication", icon: Fingerprint, perm: "audit.read" },
    { href: "/cases", label: "Cases", icon: Briefcase, perm: "handoff.handle" },
  ] },
  { label: "Knowledge", items: [
    { href: "/knowledge/documents", label: "Documents", icon: FileText, perm: "knowledge.read" },
    { href: "/knowledge/search", label: "Knowledge search", icon: FileSearch, perm: "knowledge.read" },
  ] },
  { label: "Integrations", items: [
    { href: "/integrations", label: "Bank APIs", icon: PlugZap, perm: "integration.manage" },
    { href: "/mcp", label: "MCP servers", icon: Network, perm: "mcp.manage" },
    { href: "/tools", label: "Tools", icon: Wrench, perm: "agent.read" },
    { href: "/integrations/credentials", label: "Credentials", icon: KeyRound, perm: "integration.manage" },
  ] },
  { label: "Automation", items: [
    { href: "/policies", label: "Policies", icon: ShieldCheck, perm: "policy.manage" },
    { href: "/approvals", label: "Approvals", icon: CheckSquare, perm: "approval.execute" },
    { href: "/workflows", label: "Workflows", icon: Workflow, perm: "agent.read" },
  ] },
  { label: "Operations", items: [
    { href: "/handoff", label: "Human handoff", icon: Headset, perm: "handoff.handle" },
    { href: "/audit", label: "Audit logs", icon: ScrollText, perm: "audit.read" },
    { href: "/monitoring", label: "Monitoring", icon: Gauge, perm: "tenant.read" },
    { href: "/evaluation", label: "Evaluation", icon: FlaskConical, perm: "evaluation.run" },
  ] },
  { label: "Administration", items: [
    { href: "/agents", label: "Agents", icon: Bot, perm: "agent.read" },
    { href: "/settings/organization", label: "Organization", icon: Building2, perm: "tenant.read" },
    { href: "/settings/users", label: "Users", icon: UserCog, perm: "user.manage" },
    { href: "/settings/roles", label: "Roles", icon: ClipboardList, perm: "tenant.read" },
    { href: "/settings", label: "Settings", icon: Settings },
  ] },
];

export const MOBILE_NAV: NavItem[] = [
  { href: "/dashboard", label: "Home", icon: LayoutGrid },
  { href: "/chat", label: "Chat", icon: MessageSquareText },
  { href: "/voice", label: "Voice", icon: AudioLines },
  { href: "/customers", label: "Customers", icon: Users, perm: "customer.read" },
];

export const flatNav = (): NavItem[] => NAV.flatMap((n) => ("items" in n ? n.items : [n]));
