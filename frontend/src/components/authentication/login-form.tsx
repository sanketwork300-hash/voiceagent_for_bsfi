"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { DEMO_MODE } from "@/config/env";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

const loginSchema = z.object({
  tenant: z.string().trim().min(2, "Enter your organization ID"),
  email: z.email("Enter a valid work email"),
  password: z.string().min(1, "Enter your password"),
});
type LoginValues = z.infer<typeof loginSchema>;

function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: DEMO_MODE ? { tenant: "demo-bank", email: "admin@demo-bank.example", password: "" } : { tenant: "", email: "", password: "" },
  });
  const { register, handleSubmit, formState: { errors, isSubmitting } } = form;
  const onSubmit = handleSubmit(async (v) => {
    setError(null);
    try {
      await apiClient.auth.login(v.tenant, v.email, v.password);
      router.push(`/auth/mfa?next=${encodeURIComponent(safeNext(params.get("next")))}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.userMessage : "Sign-in failed. Try again.");
    }
  });
  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-5" aria-describedby={error ? "login-error" : undefined}>
      <div>
        <h1 className="text-section font-semibold text-strong">Sign in to the operator console</h1>
        <p className="mt-1 text-small text-muted">For bank staff. Customers can use the <a href="/assist" className="underline underline-offset-2">assistant</a>.</p>
      </div>
      <Field label="Organization ID" htmlFor="tenant" error={errors.tenant?.message}>
        <Input id="tenant" autoComplete="organization" aria-invalid={!!errors.tenant} {...register("tenant")} />
      </Field>
      <Field label="Work email" htmlFor="email" error={errors.email?.message}>
        <Input id="email" type="email" autoComplete="username" aria-invalid={!!errors.email} {...register("email")} />
      </Field>
      <Field label="Password" htmlFor="password" error={errors.password?.message}>
        <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!errors.password} {...register("password")} />
      </Field>
      {error && <p id="login-error" role="alert" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-small text-danger">{error}</p>}
      <Button type="submit" variant="primary" size="lg" loading={isSubmitting}>Continue</Button>
      {DEMO_MODE && <p className="text-meta text-muted">Demo password: DemoBank!2026secure. Other roles: supervisor@, agent@, auditor@demo-bank.example.</p>}
    </form>
  );
}

export function MfaForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) return setError("Enter the 6-digit code from your authenticator app.");
    setBusy(true);
    setError(null);
    try {
      await apiClient.auth.verifyMfa(code);
      qc.clear();
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Verification failed.");
      setCode("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="grid gap-5" noValidate>
      <div>
        <h1 className="text-section font-semibold text-strong">Verify it&apos;s you</h1>
        <p className="mt-1 text-small text-muted">Enter the 6-digit code from your authenticator app.</p>
      </div>
      <Field label="Verification code" htmlFor="mfa" error={error ?? undefined}>
        <Input id="mfa" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus value={code} aria-invalid={!!error}
               onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="h-11 text-center font-mono text-lead tracking-[0.5em]" />
      </Field>
      <Button type="submit" variant="primary" size="lg" loading={busy}>Verify and sign in</Button>
      {DEMO_MODE && <p className="text-meta text-warning">Simulated MFA: the backend has no staff MFA endpoint yet. Any 6 digits except 000000 pass.</p>}
      <a href="/auth/login" className="text-small text-muted underline underline-offset-2">Use a different account</a>
    </form>
  );
}
