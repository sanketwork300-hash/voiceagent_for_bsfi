import { type NextRequest, NextResponse } from "next/server";

/** Operator console routes require a staff session cookie. The backend still authorises every API call. */
const PUBLIC = [/^\/$/, /^\/assist(\/|$)/, /^\/auth\//, /^\/api\//, /^\/_next\//, /^\/favicon/];

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (PUBLIC.some((r) => r.test(pathname))) return NextResponse.next();
  if (!req.cookies.get("bfsi_staff")?.value) {
    const url = req.nextUrl.clone();
    url.pathname = "/auth/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
