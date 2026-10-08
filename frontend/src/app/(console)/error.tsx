"use client";
import { Button } from "@/components/ui/button";

export default function ConsoleError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto grid max-w-lg gap-3 px-6 py-16">
      <h1 className="text-section font-semibold text-strong">This view couldn&apos;t load</h1>
      <p className="text-body text-muted">The rest of the console still works. No data was changed.</p>
      {error.digest && <p className="font-mono text-meta text-subtle">Reference {error.digest}</p>}
      <div><Button onClick={reset}>Try again</Button></div>
    </div>
  );
}
