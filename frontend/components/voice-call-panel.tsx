"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Phone, RefreshCw, ShieldCheck } from "lucide-react";
import { api, dateLabel, payload, type Lead } from "@/lib/api";

type Turn = { turn: number; heard: string; reply: string; end_call: number; created_at: string };
type Assessment = { interest: "interested" | "not_interested" | "unclear" | "no_conversation";
  confidence: string; summary: string; reasoning: string; evidence: { turn: number; quote: string }[];
  training_needs: string[]; objections: string[]; follow_up: string; do_not_call: boolean; method: string };
type Analysis = { status: string; result: Assessment | null; error: string | null };
type Call = { id: string; lead_id: string; to_number: string; from_number: string; contact_name: string;
  purpose: string; training_focus: string; company_name: string; status: string; version: number;
  ready_to_call: boolean; call_sid: string | null; last_error: string | null; greeting: string;
  voice: string; transcript: Turn[]; created_at: string; analysis: Analysis };
type Config = { configured: boolean; detail: string; llm_configured: boolean; voice: string; daily_limit: number };
const ACTIVE = ["dialling", "queued", "initiated", "ringing", "in-progress", "unknown"];
const FINISHED = ["completed", "busy", "no-answer", "failed", "canceled", "not-placed"];
const INTEREST = { interested: "Interested", not_interested: "Not interested", unclear: "Interest unclear", no_conversation: "No conversation to assess" };

export default function VoiceCallPanel({ lead, mock }: { lead: Lead; mock: boolean }) {
  const firstContact = lead.contacts.find(contact => contact.phone) || lead.contacts[0];
  const [phone, setPhone] = useState(firstContact?.phone || lead.company_phone || "");
  const [name, setName] = useState(firstContact ? `${firstContact.first_name} ${firstContact.last_name}`.trim() : "");
  const [purpose, setPurpose] = useState(`Discuss whether ${lead.signals[0]?.topic || "workforce training"} would help the team at ${lead.company_name}, and ask whether they would like WTD to follow up.`);
  const [config, setConfig] = useState<Config | null>(null);
  const [calls, setCalls] = useState<Call[]>([]);
  const [current, setCurrent] = useState<Call | null>(null);
  const selected = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmedAbsent, setConfirmedAbsent] = useState(false);
  const dirty = !!current && (phone !== current.to_number || name !== current.contact_name || purpose !== current.purpose);
  const active = !!current && ACTIVE.includes(current.status);
  const finished = !!current && FINISHED.includes(current.status);
  const reviewing = finished && ["pending", "running"].includes(current?.analysis.status || "");
  const editable = !active && !busy;

  const refresh = useCallback(async () => {
    const data = await api<{ items: Call[] }>(`voice/leads/${lead.id}`);
    setCalls(data.items);
    if (selected.current) setCurrent(data.items.find(item => item.id === selected.current) || null);
  }, [lead.id]);
  const choose = useCallback((call: Call) => {
    selected.current = call.id; setCurrent(call); setPhone(call.to_number);
    setName(call.contact_name); setPurpose(call.purpose); setConfirmedAbsent(false);
  }, []);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api<Config>("voice/settings"), api<{ items: Call[] }>(`voice/leads/${lead.id}`)])
      .then(([settings, data]) => { if (!cancelled) { setConfig(settings); setCalls(data.items); if (data.items[0]) choose(data.items[0]); } })
      .catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [lead.id, choose]);
  useEffect(() => {
    if (!active && !reviewing) return;
    const timer = setInterval(() => refresh().catch(e => setError(e.message)), 3000);
    return () => clearInterval(timer);
  }, [active, reviewing, refresh]);

  async function action(operation: () => Promise<Call>) {
    if (busy) return;
    setBusy(true); setError("");
    try { choose(await operation()); await refresh(); }
    catch (e) { setError((e as Error).message); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  }
  return <section>
    <div className="notice neutral"><Phone size={18} /><div><strong>Call with WTD’s automated assistant</strong><p>The assistant uses your saved WTD knowledge, this company’s details and the call purpose below. Voice: British English.</p></div></div>
    <p className="small muted">Company number: {lead.company_phone || "Not available"}. Use the Contacts tab to retrieve it from ZoomInfo or save corrections.</p>
    {error && <div className="notice error" role="alert">{error}</div>}
    {mock && <div className="notice error">Calling is unavailable for sample leads.</div>}
    {config && !config.configured && <div className="notice error">{config.detail}</div>}
    {config && !config.llm_configured && <div className="notice neutral">Configure Gemini or Anthropic before placing a call.</div>}
    <fieldset className="email-form" disabled={!editable || mock}>
      {lead.company_phone && <button className="secondary" onClick={() => setPhone(lead.company_phone)}>Use company number · {lead.company_phone}</button>}
      {!!lead.contacts.some(contact => contact.phone) && <label>Choose contact<select aria-label="Choose call contact" defaultValue="" onChange={e => { const contact = lead.contacts[Number(e.target.value)]; if (contact) { setPhone(contact.phone); setName(`${contact.first_name} ${contact.last_name}`.trim()); } }}><option value="" disabled>Select a contact</option>{lead.contacts.map((contact, i) => contact.phone && <option key={i} value={i}>{contact.first_name} {contact.last_name} · {contact.phone}</option>)}</select></label>}
      <div className="two-cols"><label>Contact name<input value={name} onChange={e => setName(e.target.value)} maxLength={120} /></label><label>Phone number<input type="tel" placeholder="+44…" value={phone} onChange={e => setPhone(e.target.value)} maxLength={40} /></label></div>
      <label>Purpose of this call<textarea aria-label="Purpose of this call" rows={4} value={purpose} onChange={e => setPurpose(e.target.value)} maxLength={700} /></label>
      <button className="secondary" disabled={!config?.configured || !phone || purpose.trim().length < 10} onClick={() => action(() => api<Call>("voice/plans", payload("POST", { lead_id: lead.id, to_number: phone, contact_name: name, purpose })))}>Save call plan</button>
    </fieldset>
    {current && !active && <button className="text-button" disabled={busy} onClick={() => {
      selected.current = null; setCurrent(null); setPhone(firstContact?.phone || lead.company_phone || "");
      setName(firstContact ? `${firstContact.first_name} ${firstContact.last_name}`.trim() : "");
    }}>Prepare a new call with saved contact details</button>}
    {current && <>
      <div className="draft-meta" style={{ marginTop: 20 }}><span className={`badge ${current.ready_to_call ? "ready" : "draft"}`}>{current.status}</span><span className="small muted">{dirty ? "Unsaved changes — save a new plan" : `Saved · version ${current.version}`}</span></div>
      <p className="sender-note"><strong>From:</strong> {current.from_number} <strong>To:</strong> {current.to_number}</p>
      <div className="programme-note"><div><strong>Opening message</strong><p>{current.greeting}</p><p className="muted" style={{ fontSize: 11, letterSpacing: 0, marginTop: 8 }}>Relevant training: {current.training_focus}</p></div></div>
      {current.last_error && <div className="notice error">{current.last_error}</div>}
      {["draft", "ready"].includes(current.status) && <>
        <div className="approval-card"><div className="row gap"><ShieldCheck size={22} /><div><strong>Ready to call</strong><p>Approve this saved phone number and purpose. Then choose Call now.</p></div></div><button role="switch" aria-label="Ready to call" aria-checked={current.ready_to_call} className={`switch ${current.ready_to_call ? "on" : ""}`} disabled={busy || dirty || mock} onClick={() => action(() => api<Call>(`voice/calls/${current.id}/approval`, payload("POST", { expected_version: current.version, ready_to_call: !current.ready_to_call })))}><span /></button></div>
        <div className="editor-actions"><button className="primary" disabled={busy || dirty || !current.ready_to_call || mock || !config?.configured || !config.llm_configured} onClick={() => action(() => api<Call>(`voice/calls/${current.id}/start`, payload("POST", { expected_version: current.version })))}><Phone size={16} />{busy ? "Working…" : "Call now"}</button></div>
      </>}
      {active && <p className="small muted">This call is {current.status}. The status refreshes automatically. No repeat call will be placed.</p>}
      {current.call_sid && <button className="secondary" disabled={busy} onClick={() => action(() => api<Call>(`voice/calls/${current.id}/sync`, payload("POST", {})))}><RefreshCw size={15} />Check Twilio status</button>}
      {current.status === "unknown" && !current.call_sid && <div className="resolve-card"><p>Check Twilio call logs before resolving this attempt.</p><label style={{ display: "flex", flexDirection: "row", margin: "12px 0" }}><input type="checkbox" checked={confirmedAbsent} onChange={e => setConfirmedAbsent(e.target.checked)} />I verified that Twilio did not place this call.</label><button className="secondary" disabled={!confirmedAbsent || busy} onClick={() => action(() => api<Call>(`voice/calls/${current.id}/resolve`, payload("POST", { verified_not_placed: true })))}>Record as not placed</button></div>}
      {finished && <section style={{ marginTop: 24 }} aria-label="Call assessment">
        <div className="row between"><h3 className="subheading">Call summary and interest</h3><button className="secondary" disabled={busy || current.analysis.status === "running"} onClick={() => action(() => api<Call>(`voice/calls/${current.id}/analysis`, payload("POST", {})))}>{busy ? "Analysing…" : "Analyse call"}</button></div>
        {current.analysis.status === "running" && <p role="status">Preparing the call assessment…</p>}
        {current.analysis.error && <div className="notice error">{current.analysis.error}</div>}
        {current.analysis.result ? <div className="activity-card">
          <div className="row between"><strong>{INTEREST[current.analysis.result.interest]}</strong><span className="small muted">Confidence: {current.analysis.result.confidence}</span></div>
          <p>{current.analysis.result.summary}</p><p className="small muted">{current.analysis.result.reasoning}</p>
          {current.analysis.result.do_not_call && <div className="notice error">Do not call. This number is on the call suppression list.</div>}
          {!!current.analysis.result.evidence.length && <><h4>Evidence from the person</h4>{current.analysis.result.evidence.map((item, i) => <blockquote key={i} style={{ margin: "12px 0", paddingLeft: 12, borderLeft: "3px solid #6f927b" }}>“{item.quote}” <span className="small muted">· Turn {item.turn}</span></blockquote>)}</>}
          {!!current.analysis.result.training_needs.length && <><h4>Training needs mentioned</h4><ul>{current.analysis.result.training_needs.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
          {!!current.analysis.result.objections.length && <><h4>Questions or objections</h4><ul>{current.analysis.result.objections.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
          <h4>Suggested next step</h4><p>{current.analysis.result.follow_up}</p>
          <p className="small muted">{current.analysis.result.method === "ai" ? "AI assessment" : "Call record assessment"} for your review. Speech recognition may contain errors. Follow-up requires your separate approval.</p>
        </div> : current.analysis.status === "pending" && <p className="muted small">The assessment will appear after processing. You can also choose Analyse call.</p>}
      </section>}
      {!!current.transcript.length && <><h3 className="subheading">AI and human conversation</h3><p className="small muted">Human speech is transcribed automatically; check the wording before acting on it. The AI text shows responses prepared for playback.</p>{current.transcript.map(turn => <div key={turn.turn}>
        {turn.heard && <div className="activity-card" style={{ background: "#f2f5f1", marginLeft: 20 }}><strong>Human · Company respondent</strong><span className="small muted"> · Turn {turn.turn}</span><p>{turn.heard}</p></div>}
        <div className="activity-card" style={{ marginRight: 20 }}><strong>AI · WTD assistant</strong><p>{turn.reply}</p></div>
      </div>)}</>}
    </>}
    {!!calls.length && <><h3 className="subheading">Call plans and history</h3>{calls.map(call => <button key={call.id} disabled={busy} className="secondary" style={{ display: "flex", marginBottom: 8, whiteSpace: "normal" }} onClick={() => choose(call)}>{dateLabel(call.created_at)} · {call.to_number} · {call.status}</button>)}</>}
  </section>;
}