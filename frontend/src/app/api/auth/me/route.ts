import { type NextRequest, NextResponse } from "next/server";
import { profileFromCookies } from "@/lib/auth/server";

export async function GET(req: NextRequest) {
  return NextResponse.json({ profile: profileFromCookies(req) }, { headers: { "cache-control": "no-store" } });
}
