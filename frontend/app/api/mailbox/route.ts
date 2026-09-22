import { NextRequest, NextResponse } from "next/server";
import { authRequest, COOKIE, cookieOptions, sameOrigin, sessionToken } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store" };

export async function GET() {
  try {
    const upstream = await authRequest("session", "GET", undefined, await sessionToken());
    const data = await upstream.json();
    if (!upstream.ok) return NextResponse.json({ connected: false, configured: false, detail: "The backend connection is unavailable." }, { status: upstream.status, headers });
    const response = NextResponse.json(data, { headers });
    if (!data.connected) response.cookies.set(COOKIE, "", { ...cookieOptions, maxAge: 0 });
    return response;
  } catch { return NextResponse.json({ connected: false, configured: false, detail: "The backend did not respond." }, { status: 503, headers }); }
}

export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "Invalid request origin" }, { status: 403, headers });
  try {
    const upstream = await authRequest("session", "DELETE", undefined, await sessionToken());
    if (!upstream.ok) return NextResponse.json({ detail: "Could not disconnect your mailbox. Try again." }, { status: 502, headers });
    const response = NextResponse.json({ connected: false }, { headers });
    response.cookies.set(COOKIE, "", { ...cookieOptions, maxAge: 0 });
    return response;
  } catch { return NextResponse.json({ detail: "Could not disconnect your mailbox. Check the backend and try again." }, { status: 502, headers }); }
}
