import { type NextRequest, NextResponse } from "next/server";
import { MFA_PENDING_COOKIE, PROFILE_COOKIE, STAFF_COOKIE, cookieOptions, sameOrigin } from "@/lib/auth/server";

export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ message: "Request rejected." }, { status: 403 });
  const out = NextResponse.json({ ok: true });
  for (const name of [STAFF_COOKIE, PROFILE_COOKIE, MFA_PENDING_COOKIE]) out.cookies.set(name, "", cookieOptions(0));
  return out;
}
