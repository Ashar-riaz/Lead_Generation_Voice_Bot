import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

// Mailbox capabilities only: neither cookie controls access to the dashboard.
export const COOKIE = "wtd_mail_connection";
export const FLOW_COOKIE = "wtd_mail_flow";
export const MAX_AGE = 8 * 60 * 60;
export const cookieOptions = { httpOnly: true, sameSite: "lax" as const,
  secure: process.env.COOKIE_SECURE === "true" || (process.env.DASHBOARD_ORIGIN || "").startsWith("https:"), path: "/" };
export const dashboardOrigin = () => new URL(process.env.DASHBOARD_ORIGIN || "http://localhost:3000").origin;
export async function sessionToken(): Promise<string | null> {
  const value = (await cookies()).get(COOKIE)?.value || "";
  return /^[A-Za-z0-9_-]{43}$/.test(value) ? value : null;
}
export function sameOrigin(request: NextRequest): boolean {
  try { return new URL(request.headers.get("origin") || "").origin === dashboardOrigin(); }
  catch { return false; }
}
export async function authRequest(path: string, method = "GET", body?: unknown, token?: string | null) {
  const base = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  return fetch(`${base}/api/v1/auth/${path}`, { method, cache: "no-store", redirect: "error",
    headers: { "Content-Type": "application/json", "X-API-Key": process.env.API_KEY || "", ...(token ? { "X-Session-Token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(60_000) });
}
