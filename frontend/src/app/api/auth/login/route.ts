import { type NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { BACKEND_URL, MFA_PENDING_COOKIE, PROFILE_COOKIE, cookieOptions, newReference, sameOrigin } from "@/lib/auth/server";

const Body = z.object({ tenant: z.string().min(2).max(64), email: z.email(), password: z.string().min(1).max(256) });

export async function POST(req: NextRequest) {
  const reference = newReference();
  if (!sameOrigin(req)) return NextResponse.json({ message: "Request rejected." }, { status: 403 });
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ message: "Enter your organization, email and password." }, { status: 400 });
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Request-ID": reference },
      body: JSON.stringify(parsed.data), cache: "no-store",
    });
  } catch {
    return NextResponse.json({ message: "The sign-in service is unavailable. Try again shortly." }, { status: 502, headers: { "x-request-id": reference } });
  }
  if (!res.ok) {
    return NextResponse.json({ message: res.status === 401 ? "That email, password or organization isn't right." : "Sign-in failed. Try again." },
      { status: res.status === 401 ? 401 : 502, headers: { "x-request-id": reference } });
  }
  const data = (await res.json()) as { access_token: string; expires_in: number };
  // Password accepted: hold the token server-side-only until the second factor is completed.
  const out = NextResponse.json({ mfa_required: true });
  out.cookies.set(MFA_PENDING_COOKIE, data.access_token, cookieOptions(300));
  out.cookies.set(PROFILE_COOKIE, JSON.stringify({ email: parsed.data.email.toLowerCase(), tenant_slug: parsed.data.tenant }), cookieOptions(data.expires_in));
  return out;
}
