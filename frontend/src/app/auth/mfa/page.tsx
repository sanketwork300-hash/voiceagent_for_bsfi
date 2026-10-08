import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFrame } from "@/components/authentication/auth-frame";
import { MfaForm } from "@/components/authentication/login-form";

export const metadata: Metadata = { title: "Verify it's you" };

export default function MfaPage() {
  return <AuthFrame><Suspense><MfaForm /></Suspense></AuthFrame>;
}
