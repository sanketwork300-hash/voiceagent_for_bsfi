import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFrame } from "@/components/authentication/auth-frame";
import { LoginForm } from "@/components/authentication/login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return <AuthFrame><Suspense><LoginForm /></Suspense></AuthFrame>;
}
