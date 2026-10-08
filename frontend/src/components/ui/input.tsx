import * as React from "react";
import { cn } from "@/utils/cn";

const base = "w-full rounded-control border border-border-strong bg-surface px-2.5 text-body text-foreground placeholder:text-subtle transition-colors focus-visible:border-[#4a4a4a] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-strong/60 disabled:opacity-50 aria-[invalid=true]:border-danger/70";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn(base, "h-8", className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(base, "min-h-20 py-2 leading-relaxed", className)} {...props} />
));
Textarea.displayName = "Textarea";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-small font-medium text-foreground", className)} {...props} />;
}

export function Field({ label, hint, error, htmlFor, children, className }: { label: string; hint?: string; error?: string; htmlFor: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? <p id={`${htmlFor}-error`} role="alert" className="text-meta text-danger">{error}</p>
             : hint ? <p id={`${htmlFor}-hint`} className="text-meta text-muted">{hint}</p> : null}
    </div>
  );
}
