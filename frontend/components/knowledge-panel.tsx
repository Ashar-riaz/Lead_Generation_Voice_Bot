"use client";
import { useEffect, useState } from "react";
import { BookOpen, ExternalLink, Save } from "lucide-react";
import { api, payload, websiteUrl, type Knowledge } from "@/lib/api";

export default function KnowledgePanel() {
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [profile, setProfile] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Knowledge>("knowledge").then(data => { setKnowledge(data); setProfile(data.profile); }).catch(e => setError(e.message)); }, []);
  async function save() {
    setBusy(true); setError(""); setMessage("");
    try { setKnowledge(await api<Knowledge>("knowledge", payload("PUT", { profile }))); setMessage("Company knowledge saved. It will be used for new searches and drafts."); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <><div className="page-heading"><div><span className="eyebrow">YOUR COMPANY, IN CONTEXT</span><h1>Company knowledge</h1><p>The facts and programmes behind your outreach.</p></div><span className="icon-tile"><BookOpen size={24} /></span></div>
    {error && <div className="notice error" role="alert">{error}</div>}{message && <div className="notice success" role="status">{message}</div>}
    <section className="panel knowledge-editor"><div className="section-heading"><div><h2>WTD company profile</h2><p>Paste accurate company website content or edit the existing profile.</p></div><button className="primary" onClick={save} disabled={busy || !knowledge || profile === knowledge.profile || profile.trim().length < 50}><Save size={15} />{busy ? "Saving…" : "Save knowledge"}</button></div>
      <textarea aria-label="Company profile" value={profile} onChange={e => setProfile(e.target.value)} rows={18} maxLength={60000} placeholder="Loading your company profile…" />
      <p className="small muted">Updates affect new drafts when Gemini or Anthropic is configured. Template mode uses the programme catalogue. Existing saved drafts stay available for review.</p>
    </section><h2 className="catalogue-title">Programme catalogue</h2><div className="catalogue-grid">{knowledge && Object.entries(knowledge.categories).map(([key, category]) => <section className="panel catalogue-card" key={key}><span className="eyebrow">{category.label}</span>{category.programmes.map(programme => <div key={programme.name}><h3>{programme.name}</h3><p>{programme.pitch}</p>{websiteUrl(programme.url) && <a href={websiteUrl(programme.url)} target="_blank" rel="noreferrer">View programme<ExternalLink size={13} /></a>}</div>)}</section>)}</div></>;
}
