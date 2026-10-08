import { decodeJwt } from "jose";
import { type NextRequest, NextResponse } from "next/server";
import { MFA_PENDING_COOKIE, PROFILE_COOKIE, STAFF_COOKIE, cookieOptions, sameOrigin } from "@/lib/auth/server";

/**
 * MOCK second factor. The backend has no staff MFA endpoint yet, so this verifies a 6-digit code locally:
 * any 6 digits pass except 000000 (used to exercise the failure path). Replace with POST /auth/mfa/verify (TOTP/WebAuthn).
 */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ message: "Request rejected." }, { status: 403 });
  const pending = req.cookies.get(MFA_PENDING_COOKIE)?.value;
  if (!pending) return NextResponse.json({ message: "Your sign-in expired. Start again." }, { status: 401 });
  const { code } = (await req.json().catch(() => ({}))) as { code?: string };
  if (!code || !/^\d{6}$/.test(code) || code === "000000") {
    return NextResponse.json({ message: "That code didn't work. Check your authenticator and try again." }, { status: 401 });
  }
  const claims = decodeJwt(pending) as { sub: string; tenant_id: string; roles?: string[]; exp?: number };
  const extra = JSON.parse(req.cookies.get(PROFILE_COOKIE)?.value ?? "{}") as { email?: string; tenant_slug?: string };
  const profile = { user_id: claims.sub, tenant_id: claims.tenant_id, roles: claims.roles ?? [], expires_at: claims.exp ?? 0,
                    email: extra.email ?? "", tenant_slug: extra.tenant_slug ?? "" };
  const out = NextResponse.json({ profile });
  out.cookies.set(STAFF_COOKIE, pending, cookieOptions((claims.exp ?? 0) - Date.now() / 1000));
  out.cookies.set(MFA_PENDING_COOKIE, "", cookieOptions(0));
  return out;
}
