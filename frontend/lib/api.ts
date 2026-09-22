export type Mailbox = { object_id: string; tenant_id: string; name: string; email: string; account_email?: string; shared?: boolean; mailbox_key?: string };
export type Signal = { topic: string; category: string; signal_score: number; audience_strength: string; signal_date: string };
export type Contact = { person_id: string; phone_source?: string; name_source?: string; first_name: string; last_name: string; job_title: string; email: string; phone: string };
export type Lead = { id: string; run_id: string; company_id: string; company_name: string; website: string; company_phone: string; company_phone_source: string; zoominfo_company_phone: string; contact_version: number; tier: string; score: number; score_reasons: string[]; signals: Signal[]; contacts: Contact[] };
export type EmailStatus = "draft" | "ready" | "sending" | "sent" | "failed" | "unknown";
export type Draft = { id: string; run_id: string; lead_id: string; company_name: string; to_name: string; to_email: string; subject: string; body: string; programme: string; ready_to_send: boolean; approved_by: string | null; approved_mailbox: string | null; sent_by: string | null; sender_email: string | null; status: EmailStatus; version: number; sent_at: string | null; last_error: string | null; message_id: string | null };
export type Run = { id: string; query: string; status: string; mock: boolean; created_at: string; lead_count: number; email_count: number; ready_count: number; sent_count: number; error: string | null; result: { progress?: string[]; errors?: string[]; plan?: { category_label: string; intent_topics: string[]; reasoning: string; country: string; intent_filters?: Record<string, string | number> } }; leads?: Lead[]; emails?: Draft[] };
export type Settings = { zoominfo_configured: boolean; microsoft_configured: boolean; mailbox_address: string; mail_provider: string; login_required: boolean; llm_configured: boolean; llm_provider: string; daily_send_limit: number; default_country: string; sender_name: string; sender_email: string };
export type Knowledge = { profile: string; categories: Record<string, { label: string; programmes: { name: string; pitch: string; url: string }[] }> };
export type DeliveryResults = { sent_count: number; results: { email_id: string; status: string; detail: string }[] };
export type Attempt = { id: string; status: string; recipient: string; created_at: string; message_id: string; error: string | null; sender_email?: string; provider?: string };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, { ...options, cache: "no-store", headers: { "Content-Type": "application/json", ...options.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path.startsWith("emails/")) window.dispatchEvent(new Event("wtd-mailbox-expired"));
    const detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((x: { msg: string }) => x.msg).join("; ") : "Request failed";
    throw new Error(detail);
  }
  return body as T;
}
export const payload = (method: string, value: unknown): RequestInit => ({ method, body: JSON.stringify(value) });
export const company = (name: string) => name.replace(/\s*\(SAMPLE\)/g, "");
export const dateLabel = (date: string) => new Date(date).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
export function websiteUrl(value: string): string | undefined {
  try { const url = new URL(value.includes("://") ? value : `https://${value}`); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}