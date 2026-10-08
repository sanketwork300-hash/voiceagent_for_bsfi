import { type NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, STAFF_COOKIE, newReference, sameOrigin } from "@/lib/auth/server";

/**
 * Backend-for-frontend proxy. The browser never holds the staff access token: it lives in an httpOnly, SameSite=Strict
 * cookie and is attached here. Customer requests carry their own short-lived session token in X-Session-Token, and
 * then the staff cookie is deliberately NOT used, so the two identities can never mix.
 */
const PUBLIC_POSTS = new Set(["sessions"]); // creating an (anonymous) customer session needs no credentials
const HOP = new Set(["connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length"]);

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const reference = newReference();
  if (path.some((p) => p === ".." || p.includes("\\"))) {
    return NextResponse.json({ detail: "invalid path" }, { status: 400, headers: { "x-request-id": reference } });
  }
  if (req.method !== "GET" && !sameOrigin(req)) {
    return NextResponse.json({ detail: "cross-origin request rejected" }, { status: 403, headers: { "x-request-id": reference } });
  }
  const headers = new Headers({ "X-Request-ID": reference, Accept: "application/json" });
  const ct = req.headers.get("content-type");
  if (ct) headers.set("Content-Type", ct);
  const sessionToken = req.headers.get("x-session-token");
  const staff = req.cookies.get(STAFF_COOKIE)?.value;
  if (sessionToken) headers.set("Authorization", `Bearer ${sessionToken}`);
  else if (staff) headers.set("Authorization", `Bearer ${staff}`);
  else if (!(req.method === "POST" && PUBLIC_POSTS.has(path.join("/")))) {
    // let the backend produce its own 401 for consistency
  }
  const url = new URL(`${BACKEND_URL}/${path.map(encodeURIComponent).join("/")}`);
  req.nextUrl.searchParams.forEach((v, k) => url.searchParams.append(k, v));
  let res: Response;
  try {
    res = await fetch(url, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return NextResponse.json({ detail: "backend unreachable" }, { status: 502, headers: { "x-request-id": reference } });
  }
  const out = new Headers({ "x-request-id": reference, "cache-control": "no-store" });
  res.headers.forEach((v, k) => {
    if (!HOP.has(k.toLowerCase()) && k.toLowerCase() !== "set-cookie") out.set(k, v);
  });
  return new NextResponse(res.status === 204 ? null : await res.arrayBuffer(), { status: res.status, headers: out });
}

export { forward as GET, forward as POST, forward as PATCH, forward as PUT, forward as DELETE };
