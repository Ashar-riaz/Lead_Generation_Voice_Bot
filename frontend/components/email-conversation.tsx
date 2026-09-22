"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mail, RefreshCw, Reply, Send, ShieldCheck } from "lucide-react";
import { api, dateLabel, payload, type Draft, type Mailbox } from "@/lib/api";

type Address = { address: string; name: string };
type Message = { id: string; subject: string; from_address: string; from_name: string;
  to: Address[]; reply_to: Address[]; body: string; body_truncated: boolean;
  direction: "inbound" | "outbound"; timestamp: string; can_reply: boolean };
type ResponseDraft = { id: string; message_id: string; body: string; recipients: Address[];
  status: string; version: number; ready_to_send: boolean; last_error: string | null; updated_at: string };
type Conversation = { state: string; synced_at: string | null; more_available: boolean;
  messages: Message[]; replies: ResponseDraft[]; mailbox: string };
const addressLabel = (items: Address[]) => items.map(item => item.name ? `${item.name} <${item.address}>` : item.address).join(", ");

export default function EmailConversation({ email, mailbox, onConnect }: {
  email?: Draft; mailbox: Mailbox | null; onConnect: () => void;
}) {
  const [data, setData] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState<ResponseDraft | null>(null);
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [verified, setVerified] = useState(false);
  const syncLock = useRef(false);
  const mounted = useRef(true);
  const eligible = !!email && email.status === "sent" && !!mailbox && email.sent_by === mailbox.object_id && email.sender_email?.toLowerCase() === mailbox.email.toLowerCase();
  const base = `emails/${email?.id}`;
  const dirty = !!draft && body !== draft.body;
  const locked = !!draft && ["sending", "sent", "unknown"].includes(draft.status);

  const refresh = useCallback(async (sync = false) => {
    if (!eligible || syncLock.current) return;
    syncLock.current = true;
    if (mounted.current) setSyncing(true);
    try {
      const result = await api<Conversation>(`${base}/conversation${sync ? "/sync" : ""}`, sync ? payload("POST", {}) : {});
      if (mounted.current) {
        setData(result); setError("");
        // Refresh an in-flight outcome without replacing unsaved text in the editor.
        setDraft(current => current?.status === "sending" ? result.replies.find(r => r.id === current.id) || current : current);
      }
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { syncLock.current = false; if (mounted.current) setSyncing(false); }
  }, [base, eligible]);

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    void (async () => { await refresh(); if (!cancelled) await refresh(true); })();
    const timer = setInterval(() => { if (!document.hidden) void refresh(true); }, 30000);
    return () => { cancelled = true; mounted.current = false; clearInterval(timer); };
  }, [refresh]);

  function choose(value: ResponseDraft) {
    setDraft(value); setBody(value.body); setVerified(false); setNotice("");
  }

  async function action(operation: () => Promise<ResponseDraft>, success = "") {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await operation();
      if (mounted.current) {
        choose(result);
        setNotice(result.status === "sent" ? "Reply accepted by Microsoft. It stays in the same email conversation." : success);
        if (result.last_error) setError(result.last_error);
      }
      await refresh();
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
      // A lost send response must show the saved outcome before a user retries.
      try {
        const latest = await api<Conversation>(`${base}/conversation`);
        if (mounted.current) {
          setData(latest);
          setDraft(current => {
            const saved = latest.replies.find(r => r.id === current?.id);
            return saved && ["sending", "sent", "unknown"].includes(saved.status) ? saved : current;
          });
        }
      } catch { /* Preserve the original actionable error. */ }
    } finally { if (mounted.current) setBusy(false); }
  }

  if (!email || email.status !== "sent") return <div className="empty-state compact"><Mail size={30} /><h3>Replies appear after sending</h3><p>Send your reviewed email first, then return here to see the contact’s response.</p></div>;
  if (!mailbox || email.sent_by !== mailbox.object_id || email.sender_email?.toLowerCase() !== mailbox.email.toLowerCase()) return <div className="notice neutral"><div><strong>Connect the sending mailbox</strong><p>Use {email.sender_email || "the Microsoft mailbox that sent this email"} to read this conversation and reply.</p><button className="secondary" onClick={onConnect}>Open mailbox connections</button></div></div>;

  return <section aria-label="Email conversation">
    <div className="row between" style={{ gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
      <div><h3>Conversation</h3><p className="small muted">{data?.synced_at ? `Checked ${dateLabel(data.synced_at)}` : "Checking for responses…"} · Refreshes every 30 seconds while open</p></div>
      <button className="secondary" disabled={syncing || busy} onClick={() => refresh(true)}><RefreshCw size={15} />{syncing ? "Checking…" : data?.more_available ? "Load more messages" : "Refresh replies"}</button>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {notice && <div className="notice success" role="status">{notice}</div>}
    {data?.state === "not_found" && <div className="notice neutral">The original email has not been found in Outlook Sent Items yet. Allow time after sending and refresh. Keep the original sent message in Sent Items until the conversation is linked.</div>}
    {data?.state === "searching" && <div className="notice neutral">Checking more sent messages for this conversation. Choose Load more messages to continue now.</div>}
    {data?.more_available && data.state !== "searching" && <p className="small muted">More messages are available. Load the next page to see the rest of the conversation.</p>}
    {data?.state === "synced" && !data.messages.some(message => message.direction === "inbound") && <div className="notice neutral">No incoming response was found in this conversation at the last check.</div>}
    {data?.messages.map(message => {
      const saved = data.replies.find(reply => reply.message_id === message.id);
      return <article className="activity-card" key={message.id} style={{ borderLeft: `3px solid ${message.direction === "inbound" ? "var(--green)" : "var(--border)"}` }}>
        <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}><strong>{message.direction === "inbound" ? "Received" : "Sent"}</strong><span className="small muted">{message.timestamp ? dateLabel(message.timestamp) : ""}</span></div>
        <p style={{ overflowWrap: "anywhere" }}><strong>From:</strong> {message.from_name} {message.from_address}</p>
        <p className="small muted" style={{ overflowWrap: "anywhere" }}><strong>To:</strong> {addressLabel(message.to)}</p>
        <p><strong>{message.subject}</strong></p>
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 300, overflowY: "auto" }}>{message.body}</p>
        {message.body_truncated && <p className="small muted">This long message is shortened here. Open Outlook to read it in full.</p>}
        {message.can_reply && <button className="secondary" disabled={busy} onClick={() => action(() => api<ResponseDraft>(`${base}/replies`, payload("POST", { message_id: message.id })))}><Reply size={15} />{saved ? "Open saved response" : "Reply"}</button>}
      </article>;
    })}
    {draft && <section aria-label="Reply editor" style={{ marginTop: 24 }}>
      <div className="draft-meta"><h3>Your response</h3><span className={`badge ${draft.status}`}>{draft.status}</span></div>
      <p className="sender-note"><strong>From:</strong> {mailbox.email}<br /><strong>Reply to:</strong> {addressLabel(draft.recipients)}</p>
      <p className="small muted">This replies to the selected message in the same conversation. Review every recipient shown above.</p>
      <fieldset className="email-form" disabled={busy || locked} style={{ marginTop: 14 }}><label>Your reply<textarea aria-label="Your reply" rows={8} value={body} maxLength={20000} onChange={e => setBody(e.target.value)} /></label></fieldset>
      {draft.last_error && <div className="notice error">{draft.last_error}</div>}
      {!locked && <>
        <button className="secondary" disabled={busy || !dirty || !body.trim()} onClick={() => action(() => api<ResponseDraft>(`${base}/replies/${draft.id}`, payload("PATCH", { expected_version: draft.version, body })), "Response saved. Review it before approving.")}>Save reply</button>
        <div className="approval-card" style={{ marginTop: 16 }}><div className="row gap"><ShieldCheck size={22} /><div><strong>Ready to send reply</strong><p>{dirty ? "Save your changes before approving." : "Approve this response and the displayed recipients."}</p></div></div><button role="switch" aria-label="Ready to send reply" aria-checked={draft.ready_to_send} className={`switch ${draft.ready_to_send ? "on" : ""}`} disabled={busy || dirty || !draft.body.trim()} onClick={() => action(() => api<ResponseDraft>(`${base}/replies/${draft.id}/approval`, payload("POST", { expected_version: draft.version, ready_to_send: !draft.ready_to_send })))}><span /></button></div>
        <button className="primary" disabled={busy || dirty || !draft.ready_to_send} onClick={() => action(() => api<ResponseDraft>(`${base}/replies/${draft.id}/send`, payload("POST", { expected_version: draft.version })))}><Send size={15} />{busy ? "Working…" : "Send reply"}</button>
      </>}
      {draft.status === "sent" && <div className="notice success">Your reply was accepted by Microsoft. Refresh the conversation to see its Sent Items copy.</div>}
      {draft.status === "sending" && <div className="notice neutral">Your reply is being sent. Refreshing will not send it again.</div>}
      {draft.status === "unknown" && <div className="resolve-card"><p>Check Outlook Sent Items or Exchange message trace before choosing the delivery outcome.</p><label style={{ display: "flex", flexDirection: "row", margin: "12px 0" }}><input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)} />I checked the outcome in Outlook or with our administrator.</label><div className="row gap" style={{ flexWrap: "wrap" }}>{[true, false].map(delivered => <button key={String(delivered)} className="secondary" disabled={busy || !verified} onClick={() => action(() => api<ResponseDraft>(`${base}/replies/${draft.id}/resolve`, payload("POST", { expected_version: draft.version, delivered })))}>{delivered ? "Verified: accepted" : "Verified: not accepted"}</button>)}</div></div>}
    </section>}
    {!!data?.replies.length && <><h3 className="subheading">Your saved responses</h3>{data.replies.map(reply => <button key={reply.id} className="secondary" style={{ display: "flex", marginBottom: 8, whiteSpace: "normal" }} disabled={busy} onClick={() => choose(reply)}>{dateLabel(reply.updated_at)} · {reply.status} · {reply.body.slice(0, 55) || "New response"}</button>)}</>}
  </section>;
}