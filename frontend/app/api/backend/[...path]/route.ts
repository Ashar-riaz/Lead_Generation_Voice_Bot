import { NextRequest, NextResponse } from "next/server";
import { sessionToken, sameOrigin } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
  const token = await sessionToken();
  if (request.method !== "GET" && !sameOrigin(request)) return NextResponse.json({ detail: "Invalid request origin" }, { status: 403 });
  const { path } = await context.params;
  if (path[0] === "auth") return NextResponse.json({ detail: "Use the mailbox connection flow" }, { status: 403 });
  if (!path.length || path.some(p => !/^[a-zA-Z0-9_-]+$/.test(p))) return NextResponse.json({ detail: "Invalid API path" }, { status: 400 });
  try {
    const base = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
    const upstream = await fetch(`${base}/api/v1/${path.join("/")}${request.nextUrl.search}`, {
      method: request.method, cache: "no-store", redirect: "error",
      headers: { "Content-Type": "application/json", "X-API-Key": process.env.API_KEY || "", ...(token ? { "X-Session-Token": token } : {}) },
      body: request.method === "GET" ? undefined : await request.text(),
      signal: AbortSignal.timeout(120_000),
    });
    const headers = new Headers({ "Content-Type": upstream.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" });
    const download = upstream.headers.get("Content-Disposition");
    if (download) headers.set("Content-Disposition", download);
    return new NextResponse(upstream.body, { status: upstream.status, headers });
  } catch {
    return NextResponse.json({ detail: "The backend did not respond. Check it is running. If sending was in progress, refresh email status before trying again." }, { status: 502 });
  }
}
export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
