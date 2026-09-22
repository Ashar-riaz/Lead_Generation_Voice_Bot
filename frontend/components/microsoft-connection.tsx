"use client";
import { useEffect, useState } from "react";
import { Mail, Plug, Unplug } from "lucide-react";
import { api, payload, type Mailbox } from "@/lib/api";

type Props = { targetAddress?: string; mailbox: Mailbox | null; configured: boolean; disabled?: boolean; onChange: () => Promise<void> };

export default function MicrosoftConnection({ targetAddress, mailbox, configured, disabled, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("mail_error")) {
      setError("Microsoft did not connect the mailbox. Check your app permissions and Microsoft consent, then try again. You can continue searching and editing drafts.");
    }
    if (url.searchParams.has("mail_error") || url.searchParams.has("mail_connected")) {
      url.searchParams.delete("mail_error"); url.searchParams.delete("mail_connected");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    }
  }, []);

  async function connect() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/auth/microsoft/start", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not connect your mailbox.");
      window.location.assign(data.url);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  async function disconnect() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/mailbox", { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not disconnect your mailbox.");
      await onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="panel connection-card">
    <div className="row between"><span className="icon-tile"><Mail size={23} /></span><span className={`badge ${mailbox ? "ready" : "draft"}`}>{mailbox ? "Account connected" : "Not connected"}</span></div>
    <h2>Microsoft email</h2>
    <p>{mailbox ? `Selected sender: ${mailbox.email}. Review and approve each message before sending.` : "Optional. Connect your Microsoft work mailbox when you are ready to send reviewed emails."}</p>
    {targetAddress && <p><strong>Company mailbox: {targetAddress}</strong><br />Your Microsoft administrator must grant your account Full Access and Send As. Connect your own authorised account below.</p>}
    {mailbox?.shared && <p className="small muted">Connected account: {mailbox.account_email}. Mailbox permissions are checked when you access mail.</p>}
    {mailbox && <button className="secondary" disabled={busy || disabled} onClick={async () => {
      setBusy(true); setError(""); setMessage("");
      try { const result = await api<{message: string}>("mailbox/check", payload("POST", {})); setMessage(result.message); }
      catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>Check mailbox access</button>}
    {message && <div className="notice success" role="status">{message}</div>}
    {error && <div className="notice error" role="alert">{error}</div>}
    {!configured && <p className="small muted">Email setup is needed before connecting. Follow docs/MICROSOFT_SETUP.md in the project.</p>}
    {mailbox ? <button className="secondary" disabled={busy || disabled} onClick={disconnect}><Unplug size={16} />{busy ? "Disconnecting…" : "Disconnect mailbox"}</button>
      : <button className="primary" disabled={!configured || busy || disabled} onClick={connect}><Plug size={16} />{busy ? "Connecting…" : "Connect Microsoft mailbox"}</button>}
    <p className="small muted">Connecting a mailbox does not send any email.</p>
  </section>;
}