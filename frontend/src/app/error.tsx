"use client";
import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";

export default function GlobalRouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { /* error details go to the server log via the digest; never to the console with user data */ }, [error]);
  return (
    <div className="mx-auto grid max-w-md gap-3 px-6 py-24">
      <h1 className="text-section font-semibold text-strong">This page couldn&apos;t load</h1>
      <p className="text-body text-muted">Something went wrong on our side. No customer data or account was changed.</p>
      {error.digest && <p className="font-mono text-meta text-subtle">Reference {error.digest}</p>}
      <div className="flex gap-2"><Button onClick={reset}>Try again</Button><Button variant="ghost" asChild><Link href="/">Go home</Link></Button></div>
    </div>
  );
}
