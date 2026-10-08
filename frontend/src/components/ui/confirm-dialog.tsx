"use client";
import * as A from "@radix-ui/react-alert-dialog";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Button } from "./button";

/**
 * Explicit confirmation for destructive / sensitive actions: explanation + consequence + clearly labelled buttons.
 * The cancel button receives focus by default so Enter never triggers the destructive action accidentally.
 */
export function ConfirmDialog({ trigger, title, description, consequence, confirmLabel, tone = "danger", onConfirm, open, onOpenChange }: {
  trigger?: React.ReactNode; title: string; description: React.ReactNode; consequence?: string; confirmLabel: string;
  tone?: "danger" | "primary"; onConfirm: () => Promise<unknown> | unknown; open?: boolean; onOpenChange?: (o: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = open ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  return (
    <A.Root open={isOpen} onOpenChange={setOpen}>
      {trigger && <A.Trigger asChild>{trigger}</A.Trigger>}
      <A.Portal>
        <A.Overlay className="fixed inset-0 z-[var(--z-overlay)] bg-black/70" />
        <A.Content className="fixed left-1/2 top-1/2 z-[var(--z-modal)] w-[calc(100%-32px)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-panel border border-border-strong bg-surface p-5 shadow-2xl shadow-black/60">
          <div className="flex gap-3">
            {tone === "danger" && <AlertTriangle aria-hidden className="mt-0.5 size-5 shrink-0 text-danger" />}
            <div className="grid gap-2">
              <A.Title className="text-lead font-semibold text-strong">{title}</A.Title>
              <A.Description asChild><div className="text-small text-muted">{description}</div></A.Description>
              {consequence && <p className="text-small text-foreground">{consequence}</p>}
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <A.Cancel asChild><Button variant="secondary" autoFocus>Cancel</Button></A.Cancel>
            <Button variant={tone === "danger" ? "danger" : "primary"} loading={busy}
              onClick={async () => { setBusy(true); try { await onConfirm(); setOpen(false); } finally { setBusy(false); } }}>
              {confirmLabel}
            </Button>
          </div>
        </A.Content>
      </A.Portal>
    </A.Root>
  );
}
