const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2, minimumFractionDigits: 0 });

export function formatINR(value: unknown): string {
  const n = typeof value === "string" ? Number(value) : (value as number);
  return typeof n === "number" && Number.isFinite(n) ? INR.format(n) : "—";
}

export function formatDate(value: string | number | Date | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "2-digit", month: "short", year: "numeric" }): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-IN", opts);
}

export function formatTime(value: string | number | Date | null | undefined, seconds = true): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) });
}

export function formatRelative(value: string | number | Date | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const diff = Math.round((now - new Date(value).getTime()) / 1000);
  if (diff < 10) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return formatDate(value);
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

export function formatPercent(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** Mask everything except the last `keep` characters of an identifier. Never reveals more than the backend sent. */
export function maskId(value: string | null | undefined, keep = 4): string {
  if (!value) return "—";
  const clean = value.replace(/\s/g, "");
  if (/^[X*]+\d{2,4}$/i.test(clean)) return `•••• ${clean.slice(-4)}`;
  return clean.length <= keep ? "••••" : `•••• ${clean.slice(-keep)}`;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export const LANGUAGE_NAMES: Record<string, { english: string; native: string }> = {
  en: { english: "English", native: "English" },
  hi: { english: "Hindi", native: "हिन्दी" },
  mr: { english: "Marathi", native: "मराठी" },
  ta: { english: "Tamil", native: "தமிழ்" },
  te: { english: "Telugu", native: "తెలుగు" },
  bn: { english: "Bengali", native: "বাংলা" },
  kn: { english: "Kannada", native: "ಕನ್ನಡ" },
  gu: { english: "Gujarati", native: "ગુજરાતી" },
  pa: { english: "Punjabi", native: "ਪੰਜਾਬੀ" },
  ml: { english: "Malayalam", native: "മലയാളം" },
};

export function languageLabel(code: string | null | undefined, tag?: string | null): string {
  if (!code) return "—";
  const name = LANGUAGE_NAMES[code]?.english ?? code;
  return tag?.endsWith("-Latn") && code === "hi" ? "Hinglish" : name;
}
