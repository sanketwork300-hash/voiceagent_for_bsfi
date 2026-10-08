"use client";
import { AlertCircle, RotateCw } from "lucide-react";
import { Component, type ReactNode } from "react";
import { ApiError } from "@/lib/api/errors";
import { cn } from "@/utils/cn";
import { Button } from "./button";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton rounded-[5px]", className)} />;
}

export function SkeletonRows({ rows = 5, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="grid gap-2 py-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 w-1/4" /><Skeleton className="h-4 w-1/3" /><Skeleton className="h-4 flex-1" />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, description, action, icon }: { title: string; description: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-panel border border-dashed border-border-strong px-6 py-10 sm:items-center sm:text-center">
      {icon && <div className="text-subtle [&_svg]:size-5">{icon}</div>}
      <p className="text-lead font-medium text-foreground">{title}</p>
      <p className="max-w-md text-small text-muted">{description}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Never shows raw status codes to users; always offers a way forward and a support reference. */
export function ErrorState({ error, title, onRetry, compact }: { error: unknown; title?: string; onRetry?: () => void; compact?: boolean }) {
  const api = error instanceof ApiError ? error : null;
  const message = api?.userMessage ?? "Something went wrong while loading this. Nothing was changed.";
  return (
    <div role="alert" className={cn("flex items-start gap-3 rounded-panel border border-border-strong bg-surface", compact ? "p-3" : "p-5")}>
      <AlertCircle aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="grid gap-1">
        <p className="text-small font-medium text-foreground">{title ?? "Couldn't load this"}</p>
        <p className="text-small text-muted">{message}</p>
        {api?.reference && <p className="font-mono text-meta text-subtle">Reference {api.reference}</p>}
        {onRetry && <div className="mt-2"><Button size="sm" onClick={onRetry}><RotateCw /> Try again</Button></div>}
      </div>
    </div>
  );
}

/** Isolates a widget: one failing panel never takes down the page. */
export class WidgetBoundary extends Component<{ children: ReactNode; title: string }, { error: unknown; key: number }> {
  state = { error: null as unknown, key: 0 };
  static getDerivedStateFromError(error: unknown) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return <ErrorState compact error={this.state.error} title={`${this.props.title} unavailable`}
        onRetry={() => this.setState((s) => ({ error: null, key: s.key + 1 }))} />;
    }
    return <div key={this.state.key} className="contents">{this.props.children}</div>;
  }
}
