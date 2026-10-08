import "server-only";
import { decodeJwt } from "jose";
import type { NextRequest } from "next/server";
import type { StaffProfile } from "@/types/domain";

export const STAFF_COOKIE = "bfsi_staff";
export const PROFILE_COOKIE = "bfsi_profile";
export const MFA_PENDING_COOKIE = "bfsi_mfa_pending";
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export function cookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: process.env.SESSION_COOKIE_SECURE === "true",
    sameSite: "strict" as const,
    path: "/",
    maxAge: Math.max(0, Math.floor(maxAgeSeconds)),
  };
}

export function newReference(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  return "REQ-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** Decode (not verify) the staff token for UI purposes. The backend verifies it on every request. */
export function profileFromCookies(req: Pick<NextRequest, "cookies">): StaffProfile | null {
  const token = req.cookies.get(STAFF_COOKIE)?.value;
  if (!token) return null;
  try {
    const claims = decodeJwt(token) as { sub: string; tenant_id: string; roles?: string[]; exp?: number };
    if (!claims.exp || claims.exp * 1000 < Date.now()) return null;
    const extra = JSON.parse(req.cookies.get(PROFILE_COOKIE)?.value ?? "{}") as { email?: string; tenant_slug?: string };
    return { user_id: claims.sub, tenant_id: claims.tenant_id, roles: claims.roles ?? [], expires_at: claims.exp,
             email: extra.email ?? "", tenant_slug: extra.tenant_slug ?? "" };
  } catch {
    return null;
  }
}

/** CSRF defence for cookie-authenticated mutations: the request must come from our own origin. */
export function sameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return req.method === "GET" || req.method === "HEAD";
  try {
    return new URL(origin).host === req.headers.get("host");
  } catch {
    return false;
  }
}
