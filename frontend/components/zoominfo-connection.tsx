"use client";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, payload } from "@/lib/api";

type Result = { status: string; intent_topic_count: number; automatic_renewal: boolean; expires_in_seconds: number; message: string };

export default function ZoomInfoConnection({ configured }: { configured: boolean }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  async function test() {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await api<Result>("zoominfo/test-connection", payload("POST", {}))); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="connection-test">
    <button className="secondary" disabled={!configured || busy} onClick={test}><RefreshCw size={14} className={busy ? "spin" : ""} />{busy ? "Checking ZoomInfo…" : "Test ZoomInfo connection"}</button>
    {error && <p className="field-error" role="alert">{error}</p>}
    {result && <div role="status"><p><strong>Connected · {result.intent_topic_count} intent topics available</strong></p><p>{result.message}</p><p>Access tokens renew automatically as needed. At this check, the current token had about {Math.ceil(result.expires_in_seconds / 60)} minutes remaining.</p></div>}
    <p>Checks authentication and topic access. No contact enrichment or email delivery.</p>
  </div>;
}
