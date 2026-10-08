import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";
import { Providers } from "./providers";

// Self-hosted (no third-party font requests at runtime).

export const metadata: Metadata = {
  title: { default: "Ledgerline", template: "%s · Ledgerline" },
  description: "AI chat and voice agents for banks, NBFCs and insurers — with the bank's policies in control.",
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { themeColor: "#050505", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
