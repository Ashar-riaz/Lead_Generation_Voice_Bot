"use client";
import { useEffect, useRef, useState } from "react";
import { Check, ExternalLink, Mail, Save, Send, ShieldCheck, X } from "lucide-react";
import { api, company, dateLabel, payload, websiteUrl, type Attempt, type DeliveryResults, type Draft, type Lead, type Mailbox } from "@/lib/api";

import VoiceCallPanel from "./voice-call-panel";
import EmailConversation from "./email-conversation";

type Props = { lead: Lead; email?: Draft; mock: boolean; mailbox: Mailbox | null; onConnect: () => void; onClose: () => void; onChange: () => Promise<void> };
export default function EmailEditor({ lead, email, mock, mailbox, onConnect, onClose, onChange }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState(email);
  const [form, setForm] = useState({ to_email: email?.to_email || "", to_name: email?.to_name || "", subject: email?.subject || "", body: email?.body || "" });
  const [tab, setTab] = useState<"email" | "evidence" | "activity" | "call" | "replies">(email ? "email" : "evidence");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const dirty = Boolean(draft && Object.entries(form).some(([k, v]) => v !== draft[k as keyof Draft]));
  const approvedByMe = !!draft?.ready_to_send && draft.approved_by === mailbox?.object_id;
  const locked = !draft || ["sent", "sending", "unknown"].includes(draft.status);
  const url = websiteUrl(lead.website);

  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (tab === "activity" && draft) api<{ items: Attempt[] }>(`emails/${draft.id}/attempts`).then(r => setAttempts(r.items)).catch(e => setError(e.message));
  }, [tab, draft]);
  async function update(action: () => Promise<Draft>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await action(); setDraft(value);
      setForm({ to_email: value.to_email, to_name: value.to_name, subject: value.subject, body: value.body });
      setMessage(success); await onChange();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function send() {
    if (!draft || dirty) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<DeliveryResults>("emails/send-ready", payload("POST", { run_id: draft.run_id, emails: [{ email_id: draft.id, expected_version: draft.version }] }));
      setDraft(await api<Draft>(`emails/${draft.id}`));
      if (result.sent_count) setMessage("Email accepted by Microsoft Graph. Check Outlook Sent Items."); else setError(result.results[0]?.detail || "No email was sent.");
      await onChange();
    } catch (e) { setError((e as Error).message); await onChange().catch(() => {}); } finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="lead-dialog" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }} aria-labelledby="lead-title">
    <div className="drawer-top"><span className="eyebrow">COMPANY WORKSPACE</span><button className="icon-button" aria-label="Close company details" disabled={busy} onClick={onClose}><X size={21} /></button></div>
    <div className="drawer-heading"><div className="company-icon large">{company(lead.company_name).slice(0, 2).toUpperCase()}</div><div><h2 id="lead-title">{company(lead.company_name)}</h2>{url && <a href={url} target="_blank" rel="noreferrer" className="muted small">{lead.website}<ExternalLink size={12} /></a>}</div><span className={`badge ${lead.tier.toLowerCase()}`}>{lead.tier} · {lead.score}</span></div>
    <div className="tabs" style={{ flexWrap: "wrap", gap: "0 20px" }}>{([['email', 'Email draft'], ['evidence', 'Lead evidence'], ['activity', 'Delivery history'], ['call', 'Voice call'], ['replies', 'Replies']] as const).map(([key, label]) => <button style={{ flexShrink: 0 }} className={tab === key ? "active" : ""} key={key} onClick={() => setTab(key)}>{label}</button>)}</div>
    <div className="drawer-body">
      {tab === "call" && <VoiceCallPanel lead={lead} mock={mock} />}
      {tab === "replies" && <EmailConversation key={`${lead.id}-${mailbox?.object_id || "none"}`} email={draft} mailbox={mailbox} onConnect={onConnect} />}
      {error && <div className="notice error" role="alert">{error}{draft && <button className="text-button" onClick={() => update(() => api<Draft>(`emails/${draft.id}`), "Latest saved draft loaded.")}>Reload saved draft</button>}</div>}
      {message && <div className="notice success" role="status">{message}</div>}
      {tab === "email" && (draft ? <>
        <div className="draft-meta"><span className={`badge ${draft.status}`}>{draft.status === "ready" ? "Ready to send" : draft.status}</span><span className="small muted">{dirty ? "Unsaved changes" : `Saved · version ${draft.version}`}</span></div>
        {draft.last_error && <div className="notice error">{draft.last_error}</div>}
        <div className="programme-note"><Mail size={17} /><div><span className="small muted">MATCHED PROGRAMME</span><strong>{draft.programme}</strong></div></div>
        <p className="sender-note"><strong>From:</strong> {locked ? (draft?.sender_email || "Recorded sender") : (mailbox?.email || "Connect your Microsoft mailbox to send")} · Microsoft 365</p>
        <fieldset disabled={locked || busy} className="email-form"><div className="two-cols"><label>Recipient name<input value={form.to_name} onChange={e => setForm({ ...form, to_name: e.target.value })} maxLength={200} /></label><label>Email address<input type="email" value={form.to_email} onChange={e => setForm({ ...form, to_email: e.target.value })} required /></label></div><label>Subject<input value={form.subject} onChange={e => setForm({ ...form, subject: e.target.value })} maxLength={240} required /></label><label>Message<textarea className="message-input" value={form.body} onChange={e => setForm({ ...form, body: e.target.value })} maxLength={20000} required /></label></fieldset>
        {draft.status === "sent" ? <div className="notice success"><Check size={17} />Sent {draft.sent_at ? dateLabel(draft.sent_at) : ""}. Inbox delivery is not tracked.</div> : draft.status === "unknown" ? <div className="resolve-card"><strong>Confirm the delivery outcome</strong><p>Check the sender’s Outlook Sent Items or ask your administrator to check Exchange message trace before choosing an outcome. App delivery reference: <code>{draft.message_id}</code>.</p><div className="row gap"><button disabled={busy || (draft.sent_by !== mailbox?.object_id)} className="secondary" onClick={() => update(() => api<Draft>(`emails/${draft.id}/resolve`, payload("POST", { expected_version: draft.version, delivered: true })), "Recorded as sent after your verification.")}>Verified: accepted</button><button disabled={busy || (draft.sent_by !== mailbox?.object_id)} className="secondary" onClick={() => update(() => api<Draft>(`emails/${draft.id}/resolve`, payload("POST", { expected_version: draft.version, delivered: false })), "Recorded as not accepted. Review and approve again to retry.")}>Verified: not accepted</button></div></div> : <>
          <div className="approval-card"><div className="row gap"><ShieldCheck size={22} /><div><strong>Ready to send</strong><p>{dirty ? "Save your changes before approving this email." : draft.ready_to_send && !approvedByMe ? "Another account approved this draft. Approve it yourself to send from your mailbox." : mailbox ? `Approve this recipient and message to send from ${mailbox.email}.` : "Connect a Microsoft mailbox before approving this draft for sending."}</p></div></div><button role="switch" aria-label="Ready to send" aria-checked={approvedByMe} className={`switch ${approvedByMe ? "on" : ""}`} disabled={busy || dirty || locked || !mailbox} onClick={() => update(() => api<Draft>(`emails/${draft.id}/approval`, payload("POST", { expected_version: draft.version, ready_to_send: !approvedByMe })), approvedByMe ? "Approval removed. This email will not send." : "Approved. Use Send email when you are ready.")}><span /></button></div>
          <p className="small muted">{mock ? "This is sample data. Approval can be tested, but email delivery is disabled." : !mailbox ? "Connect a Microsoft mailbox in Connections when you are ready to send." : "Saving or approving a draft does not send it. Only approved drafts can be sent."}</p>
          {!mailbox && !mock && <button className="secondary" onClick={onConnect}>Open mailbox connections</button>}
          <div className="editor-actions"><button className="secondary" disabled={busy || !dirty || locked} onClick={() => update(() => api<Draft>(`emails/${draft.id}`, payload("PATCH", { ...form, expected_version: draft.version })), "Changes saved. Please review and approve the new version.")}><Save size={16} />Save draft</button><button className="primary" disabled={busy || dirty || !approvedByMe || mock || !mailbox || locked} onClick={send}><Send size={16} />{busy ? "Working…" : "Send email"}</button></div>
        </>}
      </> : <div className="empty-state compact"><Mail size={34} /><h3>No email draft available</h3><p>This search may have skipped email drafting, or no contact email was found. Run another search with contact enrichment and email drafts enabled.</p></div>)}
      {tab === "evidence" && <><div className="notice neutral"><strong>{mock ? "Fictional sample evidence" : "Research interest · training need unconfirmed"}</strong><p>{mock ? "These companies and signals are included to demonstrate the workflow." : "ZoomInfo indicates interest in these topics. It does not confirm an approved training budget, a request for training, or the number of employees to train."}</p></div>
        <h3 className="subheading">Intent signals</h3>{lead.signals.map((signal, i) => <div className="signal-card" key={i}><div className="row between"><strong>{signal.topic}</strong><span className="signal-number">{signal.signal_score}<small>/100</small></span></div><div className="signal-bar"><span style={{ width: `${Math.max(0, Math.min(100, signal.signal_score))}%` }} /></div><div className="row between small muted"><span>Audience strength: {signal.audience_strength || "Not provided"}</span><span>{signal.signal_date || "Date not provided"}</span></div><span className="small muted">Source: {mock ? "project sample data" : "ZoomInfo Intent API"}</span></div>)}
        <h3 className="subheading">Why this lead scored {lead.score}</h3><ul className="reason-list">{lead.score_reasons.map((reason, i) => <li key={i}><Check size={14} />{reason}</li>)}</ul>
        <h3 className="subheading">Contacts</h3>{lead.contacts.length ? lead.contacts.map((contact, i) => <div className="contact-card" key={i}><strong>{contact.first_name} {contact.last_name}</strong><span>{contact.job_title || "Role not provided"}</span><span className="small muted">{contact.email || "Email not available"}{contact.phone && ` · ${contact.phone}`}</span></div>) : <p className="muted">No contacts found.</p>}
      </>}
      {tab === "activity" && <><h3 className="subheading">Delivery attempts</h3><p className="muted small">Only an explicit send action creates a delivery attempt.</p>{attempts.length ? attempts.map(attempt => <div className="activity-card" key={attempt.id}><div className="row between"><span className={`badge ${attempt.status}`}>{attempt.status}</span><span className="small muted">{dateLabel(attempt.created_at)}</span></div><p>{attempt.recipient}</p>{attempt.sender_email && <p className="small muted">From: {attempt.sender_email}</p>}<code>{attempt.message_id}</code>{attempt.error && <p className="small">{attempt.error}</p>}</div>) : <div className="empty-state compact"><ShieldCheck size={30} /><h3>No email has been sent</h3><p>Your draft stays here until you approve it and choose Send.</p></div>}</>}
    </div>
  </dialog>;
}
