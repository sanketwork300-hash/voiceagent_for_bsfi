import { SignJWT } from "jose";
import { type NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, newReference, sameOrigin } from "@/lib/auth/server";

/**
 * DEMO ONLY — stands in for the institution's identity provider (mobile app / netbanking login), which in production
 * issues the signed customer assertion. Disabled unless DEMO_MODE=true, and limited to the sandbox customers.
 */
const DEMO_CUSTOMERS = new Set(["CUST1001", "CUST1002"]);

export async function POST(req: NextRequest) {
  if (process.env.DEMO_MODE !== "true") return NextResponse.json({ message: "Not found" }, { status: 404 });
  if (!sameOrigin(req)) return NextResponse.json({ message: "Request rejected." }, { status: 403 });
  const { customer_id = "CUST1001" } = (await req.json().catch(() => ({}))) as { customer_id?: string };
  if (!DEMO_CUSTOMERS.has(customer_id)) return NextResponse.json({ message: "Unknown demo customer." }, { status: 400 });
  const tenant = process.env.DEMO_TENANT ?? "demo-bank";
  const secret = new TextEncoder().encode(process.env.DEMO_CUSTOMER_ASSERTION_SECRET ?? "");
  const assertion = await new SignJWT({ amr: ["pwd", "otp"] })
    .setProtectedHeader({ alg: "HS256" }).setSubject(customer_id).setAudience(tenant).setIssuedAt().setExpirationTime("10m").sign(secret);
  const reference = newReference();
  const res = await fetch(`${BACKEND_URL}/sessions`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Request-ID": reference },
    body: JSON.stringify({ tenant, channel: "chat", customer_assertion: assertion }), cache: "no-store",
  }).catch(() => null);
  if (!res || !res.ok) {
    return NextResponse.json({ message: "We couldn't start a secure session right now." }, { status: 502, headers: { "x-request-id": reference } });
  }
  return NextResponse.json(await res.json(), { headers: { "cache-control": "no-store" } });
}
