"use client";
import { ShieldCheck, UserRound } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { DEMO_MODE } from "@/config/env";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { useCustomerSession } from "@/store/customer-session";

/** Starts the shared customer session (used by both chat and voice). */
export function SessionGate({ audience, tenant = "demo-bank" }: { audience: "customer" | "operator"; tenant?: string }) {
  const start = useCustomerSession((s) => s.start);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const go = async (kind: "demo" | "anon", customerId?: string) => {
    setBusy(kind + (customerId ?? ""));
    setError(null);
    try {
      if (kind === "demo") start(await apiClient.sessions.createDemoAuthenticated(customerId), "demo_authenticated", customerId);
      else start(await apiClient.sessions.create(tenant), "anonymous");
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "We couldn't start a secure session right now.", null));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="mx-auto grid max-w-md gap-5 px-4 py-16">
      <div>
        <h2 className="text-section font-semibold text-strong">{audience === "operator" ? "Start a test conversation" : "How would you like to continue?"}</h2>
        <p className="mt-1 text-small text-muted">
          {audience === "operator"
            ? "Talk to the live agent as a sample customer. You'll see tool calls, sources and policy decisions alongside."
            : "Sign in to check your accounts, or ask general questions without signing in."}
        </p>
      </div>
      <div className="grid gap-2">
        {DEMO_MODE && (
          <Button variant="primary" size="lg" className="justify-start" loading={busy === "demoCUST1001"} onClick={() => go("demo", "CUST1001")}>
            <ShieldCheck />Sign in as Priya Sharma <span className="ml-auto text-meta opacity-70">sample customer</span>
          </Button>
        )}
        {DEMO_MODE && audience === "operator" && (
          <Button size="lg" className="justify-start" loading={busy === "demoCUST1002"} onClick={() => go("demo", "CUST1002")}>
            <ShieldCheck />Sign in as Arjun Mehta <span className="ml-auto text-meta text-muted">sample customer</span>
          </Button>
        )}
        <Button size="lg" className="justify-start" loading={busy === "anon"} onClick={() => go("anon")}>
          <UserRound />Continue without signing in
        </Button>
      </div>
      {DEMO_MODE && <p className="text-meta text-muted">Demo sign-in stands in for the bank&apos;s app or netbanking login.</p>}
      {error && <p role="alert" className="text-small text-danger">{error.userMessage}{error.reference ? ` Reference ${error.reference}.` : ""}</p>}
    </div>
  );
}
