import { NextRequest, NextResponse } from "next/server";
import { authRequest, cookieOptions, FLOW_COOKIE, sameOrigin } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "Invalid request origin" }, { status: 403 });
  try {
    const upstream = await authRequest("microsoft/start", "POST", {});
    const data = await upstream.json();
    if (!upstream.ok) return NextResponse.json({ detail: data.detail || "Could not connect your Microsoft mailbox." }, { status: upstream.status });
    const response = NextResponse.json({ url: data.authorization_url }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(FLOW_COOKIE, data.flow_handle, { ...cookieOptions, path: "/api/auth/microsoft/callback", maxAge: 600 });
    return response;
  } catch { return NextResponse.json({ detail: "Could not reach the mailbox connection service. Check the backend and try again." }, { status: 502 }); }
}
