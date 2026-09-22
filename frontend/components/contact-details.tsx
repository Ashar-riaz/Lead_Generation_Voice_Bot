"use client";
import { useEffect, useState } from "react";
import { Phone, Save } from "lucide-react";
import { api, payload, type Lead } from "@/lib/api";

export default function ContactDetails({ lead, mock, onSaved }: {
  lead: Lead; mock: boolean; onSaved: (lead: Lead) => Promise<void>;
}) {
  const [person, setPerson] = useState(lead.contacts[0]?.person_id || "");
  const selected = lead.contacts.find(c => c.person_id === person);
  const [companyPhone, setCompanyPhone] = useState(lead.company_phone || "");
  const [name, setName] = useState(selected ? `${selected.first_name} ${selected.last_name}`.trim() : "");
  const [phone, setPhone] = useState(selected?.phone || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    setCompanyPhone(lead.company_phone || "");
    const contact = lead.contacts.find(c => c.person_id === person);
    setName(contact ? `${contact.first_name} ${contact.last_name}`.trim() : "");
    setPhone(contact?.phone || "");
  }, [lead, person]);
  const dirty = companyPhone !== (lead.company_phone || "") || phone !== (selected?.phone || "") ||
    name !== (selected ? `${selected.first_name} ${selected.last_name}`.trim() : "");
  async function save() {
    setBusy(true); setError(""); setMessage("");
    try {
      const updated = await api<Lead>(`leads/${lead.id}/contact`, payload("PATCH", {
        expected_version: lead.contact_version || 1, company_phone: companyPhone,
        person_id: person || null, contact_name: name, contact_phone: phone,
      }));
      if (!person) setPerson(updated.contacts.at(-1)?.person_id || "");
      await onSaved(updated); setMessage("Contact details saved. Prepare a new call plan to use these details.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function lookup() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<{ lead: Lead; message: string }>(`leads/${lead.id}/company-phone`,
        payload("POST", { expected_version: lead.contact_version || 1 }));
      await onSaved(result.lead); setMessage(result.message);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section>
    <h3 className="subheading">Company phone and contact</h3>
    <p className="muted small">Save the company switchboard and the person’s direct number separately. Use +country code when correcting a number, for example +44.</p>
    {error && <div className="notice error" role="alert">{error}<button className="text-button" disabled={busy} onClick={async () => {
      try { await onSaved(await api<Lead>(`leads/${lead.id}`)); setError(""); }
      catch (e) { setError((e as Error).message); }
    }}>Reload saved details</button></div>}
    {message && <div className="notice success" role="status">{message}</div>}
    <fieldset className="email-form" disabled={busy}>
      <label>Company phone<input type="tel" value={companyPhone} onChange={e => setCompanyPhone(e.target.value)} maxLength={80} placeholder="+44…" /></label>
      <p className="small muted">Source: {lead.company_phone_source === "manual" ? "Your saved correction" : lead.company_phone ? "ZoomInfo" : "Not available"}.
        {lead.zoominfo_company_phone && ` Latest ZoomInfo value: ${lead.zoominfo_company_phone}`}</p>
      <button className="secondary" disabled={mock || dirty} onClick={lookup}><Phone size={16} />Get company number from ZoomInfo</button>
      <p className="small muted">This lookup may use a ZoomInfo enrichment credit. Save edits first; manual corrections are preserved.</p>
      <label>Contact<select value={person} onChange={e => setPerson(e.target.value)} disabled={dirty}>
        <option value="">Add a contact</option>{lead.contacts.map(c => <option key={c.person_id} value={c.person_id}>{c.first_name} {c.last_name}{c.job_title ? ` · ${c.job_title}` : ""}</option>)}
      </select></label>
      <div className="two-cols"><label>Contact name<input value={name} onChange={e => setName(e.target.value)} maxLength={120} /></label>
        <label>Contact phone<input type="tel" value={phone} onChange={e => setPhone(e.target.value)} maxLength={80} placeholder="+44…" /></label></div>
      <button className="secondary" disabled={!dirty} onClick={save}><Save size={16} />Save contact details</button>
    </fieldset>
    <p className="small muted">These edits update this saved lead. Email drafts keep their reviewed recipient and message.</p>
  </section>;
}