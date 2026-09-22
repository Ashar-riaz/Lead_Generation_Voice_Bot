import { NextRequest, NextResponse } from "next/server";
import { authRequest, COOKIE, cookieOptions, dashboardOrigin, FLOW_COOKIE, MAX_AGE } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  const flow = request.cookies.get(FLOW_COOKIE)?.value;
  let token: string | null = null, error = "connection_failed";
  try {
    if (flow && /^[A-Za-z0-9_-]{43}$/.test(flow)) {
      const params: Record<string, string> = {};
      for (const key of ["code", "state", "error"]) {
        const value = request.nextUrl.searchParams.get(key);
        if (value) params[key] = value;
      }
      const upstream = await authRequest("microsoft/callback", "POST", { flow_handle: flow, response: params });
      const data = await upstream.json();
      if (upstream.ok && /^[A-Za-z0-9_-]{43}$/.test(data.session_token)) token = data.session_token;
      else if (upstream.status === 403) error = "access_denied";
    }
  } catch { /* Do not expose Microsoft codes, tokens or provider error details in URLs. */ }
  const response = NextResponse.redirect(new URL(token ? "/?mail_connected=1#connections" : `/?mail_error=${error}#connections`, dashboardOrigin()), 303);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.cookies.set(FLOW_COOKIE, "", { ...cookieOptions, path: "/api/auth/microsoft/callback", maxAge: 0 });
  if (token) response.cookies.set(COOKIE, token, { ...cookieOptions, maxAge: MAX_AGE });
  else response.cookies.set(COOKIE, "", { ...cookieOptions, maxAge: 0 });
  return response;
}
