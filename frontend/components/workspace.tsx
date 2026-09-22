"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, BookOpen, Building2, Check, ChevronRight, CircleHelp, Clock3, FileText, History, LayoutGrid, Loader2, Mail, RefreshCw, Search, Send, Settings2, ShieldCheck, Sparkles, Sprout, X } from "lucide-react";
import { api, company, dateLabel, payload, type DeliveryResults, type Draft, type Lead, type Run, type Settings, type Mailbox } from "@/lib/api";
import EmailEditor from "./email-editor";
import KnowledgePanel from "./knowledge-panel";
import MicrosoftConnection from "./microsoft-connection";
import SearchPanel from "./search-panel";
import ZoomInfoConnection from "./zoominfo-connection";

type View = "leads" | "outreach" | "history" | "knowledge" | "settings";
const steps = ["plan_search", "search_companies", "find_buyers", "shortlist", "enrich_contacts", "final_score", "write_emails", "export_results"];
const names: Record<string, string> = { plan_search: "Planning your search", search_companies: "Finding companies", find_buyers: "Finding decision makers", shortlist: "Selecting relevant prospects", enrich_contacts: "Finding contact details", final_score: "Scoring leads", write_emails: "Writing email drafts", export_results: "Saving results" };

export default function Workspace() {
  const [mailbox, setMailbox] = useState<Mailbox | null>(null);
  const [view, setView] = useState<View>("leads");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runsTotal, setRunsTotal] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const [active, setActive] = useState<Run | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [tier, setTier] = useState("All");
  const [status, setStatus] = useState("all");
  const [opened, setOpened] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [delivery, setDelivery] = useState<DeliveryResults | null>(null);
  const [sendProgress, setSendProgress] = useState("");

  const refreshMailbox = useCallback(async () => {
    const response = await fetch("/api/mailbox", { cache: "no-store" });
    const data = await response.json();
    setMailbox(data.connected ? data.mailbox : null);
    if (!response.ok) throw new Error(data.detail || "Could not check the mailbox connection.");
  }, []);
  useEffect(() => {
    if (window.location.hash === "#connections") setView("settings");
    refreshMailbox().catch(() => {});
    const expire = () => setMailbox(null);
    window.addEventListener("wtd-mailbox-expired", expire);
    return () => window.removeEventListener("wtd-mailbox-expired", expire);
  }, [refreshMailbox]);

  const refreshRuns = useCallback(async () => {
    const list = await api<{ items: Run[]; total: number }>("runs?limit=100");
    setRuns(list.items); setRunsTotal(list.total);
    if (!selectedRef.current && list.items.length) setSelected(list.items[0].id);
  }, []);
  const refresh = useCallback(async () => {
    const id = selectedRef.current;
    if (id) {
      const run = await api<Run>(`runs/${id}`);
      if (selectedRef.current === id) setActive(run);
    }
    await refreshRuns();
  }, [refreshRuns]);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api<Settings>("settings"), refreshRuns()]).then(([config]) => { if (!cancelled) setSettings(config); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [refreshRuns]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false; setLoading(true); setOpened(null); setDelivery(null); setActive(null);
    api<Run>(`runs/${selected}`).then(run => { if (!cancelled) setActive(run); }).catch(e => { if (!cancelled) setError(e.message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);
  const running = active?.status === "running" || active?.status === "queued";
  useEffect(() => {
    if (!running && !sending) return;
    const timer = setInterval(() => refresh().catch(e => setError(e.message)), 2000);
    return () => clearInterval(timer);
  }, [running, sending, refresh]);

  const leads = active?.leads || [];
  const drafts = active?.emails || [];
  const emailsByLead = new Map(drafts.map(email => [email.lead_id, email]));
  const ready = drafts.filter(e => e.ready_to_send && e.status === "ready" && e.approved_by === mailbox?.object_id);
  const sent = drafts.filter(e => e.status === "sent");
  const visibleLeads = leads.filter(lead => (tier === "All" || lead.tier === tier) && `${lead.company_name} ${lead.contacts.map(c => `${c.first_name} ${c.last_name}`).join(" ")}`.toLowerCase().includes(filter.toLowerCase()) && (view !== "outreach" || (emailsByLead.has(lead.id) && (status === "all" || emailsByLead.get(lead.id)?.status === status))));
  const openLead = leads.find(l => l.id === opened);

  async function sendReady() {
    if (!active || sending || active.mock) return;
    const snapshot = ready.slice(); setSending(true); setError(""); setDelivery(null);
    const results: DeliveryResults = { results: [], sent_count: 0 };
    try {
      // One request per approved email keeps progress visible and limits request duration.
      for (const [index, email] of snapshot.entries()) {
        setSendProgress(`Sending ${index + 1} of ${snapshot.length}…`);
        const result = await api<DeliveryResults>("emails/send-ready", payload("POST", { run_id: active.id, emails: [{ email_id: email.id, expected_version: email.version }] }));
        results.sent_count += result.sent_count; results.results.push(...result.results);
        setDelivery({ ...results, results: [...results.results] });
        await refresh();
      }
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); setSendProgress(""); await refresh().catch(e => setError(e.message)); }
  }
  const loadMoreRuns = async () => {
    try { const more = await api<{ items: Run[]; total: number }>(`runs?limit=100&offset=${runs.length}`); setRuns([...runs, ...more.items]); setRunsTotal(more.total); }
    catch (e) { setError((e as Error).message); }
  };

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark"><Sprout size={22} /></span><strong>wtd<span>.</span></strong></div><div className="workspace-label">EMPLOYER PARTNERSHIPS</div><nav aria-label="Main navigation">{([
      ["leads", LayoutGrid, "Lead discovery"], ["outreach", Mail, "Email outreach"], ["history", History, "Search history"], ["knowledge", BookOpen, "Company knowledge"],
    ] as const).map(([key, Icon, label]) => <button key={key} aria-label={label} className={view === key ? "selected" : ""} onClick={() => { setView(key); setError(""); }}><Icon size={18} /><span>{label}</span>{key === "outreach" && ready.length > 0 && <span className="nav-count">{ready.length}</span>}</button>)}</nav>
      <div className="sidebar-note"><ShieldCheck size={22} /><strong>You’re in control.</strong><p>Review every message.<br />Only send when it’s ready.</p></div>
      <div className="sidebar-bottom"><button onClick={() => setView("settings")} className={view === "settings" ? "selected" : ""}><Settings2 size={18} />Connections</button><div className="profile"><span className="avatar">WT</span><div><strong>WTD workspace</strong><small>Employer partnerships</small></div></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">Workspace<ChevronRight size={13} /><span>{{ leads: "Lead discovery", outreach: "Email outreach", history: "Search history", knowledge: "Company knowledge", settings: "Connections" }[view]}</span></div><div className="row gap"><span className="top-status"><span className="dot green" />Approval required</span><button className="icon-button" aria-label="Connection help" onClick={() => setView("settings")}><CircleHelp size={18} /></button></div></header>
      <main className="main-content">
        {error && <div className="notice error" role="alert"><span>{error}</span><button className="text-button" onClick={() => { setError(""); refresh().catch(e => setError(e.message)); }}>Retry refresh</button><button className="icon-button" aria-label="Dismiss error" onClick={() => setError("")}><X size={16} /></button></div>}
        {(view === "leads" || view === "outreach") && <>
          <div className="page-heading"><div><span className="eyebrow">{view === "leads" ? "MAKE THE RIGHT CONNECTION" : "A LITTLE MORE PERSONAL"}</span><h1>{view === "leads" ? "Find your next opportunity." : "Thoughtful outreach starts here."}</h1><p>{view === "leads" ? "Discover companies exploring new skills. Start a meaningful conversation." : "Review your drafts, choose what’s ready, and send with confidence."}</p></div><span className="page-tag"><Sparkles size={14} />WTD lead workspace</span></div>
          {view === "leads" && <SearchPanel settings={settings} onError={setError} onCreated={run => { setSelected(run.id); setActive(run); setFilter(""); setTier("All"); setError(""); refreshRuns().catch(e => setError(e.message)); }} />}
          <section className="metrics" aria-label="Selected search totals">{[
            [Building2, "Companies found", leads.length, "In this search", ""], [Sparkles, "Hot prospects", leads.filter(l => l.tier === "Hot").length, "Based on intent score", "warm"], [ShieldCheck, "Ready to send", ready.length, "Reviewed and approved", "green"], [Send, "Emails sent", sent.length, "Accepted by Microsoft", "purple"],
          ].map(([Icon, label, count, note, tone]) => { const MetricIcon = Icon as typeof Building2; return <div className="metric" key={label as string}><div className="row between"><span>{label as string}</span><MetricIcon size={17} className={tone as string} /></div><strong>{count as number}</strong><small>{note as string}</small></div>; })}</section>
          <section className="panel results-panel"><div className="section-heading results-heading"><div><h2>{view === "leads" ? "Your prospects" : "Your email drafts"}<span className="count-pill">{visibleLeads.length}</span></h2><p>{active?.result.plan?.category_label || "Training opportunities"}{active?.mock ? " · Sample data" : active ? " · Live results" : ""}</p></div><div className="row gap actions-wrap"><button className="secondary small-button" disabled={!active || loading || sending} onClick={() => { setError(""); refresh().catch(e => setError(e.message)); }} aria-label="Refresh results"><RefreshCw size={14} /></button>{active?.status === "completed" && <a className="secondary small-button" href={`/api/backend/runs/${active.id}/export?format=csv`}><ArrowDownToLine size={14} />Export</a>}<button className="primary small-button" onClick={sendReady} disabled={!ready.length || sending || active?.mock || !mailbox} title={active?.mock ? "Sample emails cannot be sent" : !mailbox ? "Connect a Microsoft mailbox in Connections to send" : "Send only the approved drafts in this search"}><Send size={14} />{sending ? sendProgress : `Send ready emails${ready.length ? ` (${ready.length})` : ""}`}</button></div></div>
            {active?.mock && <div className="sample-banner" role="status"><strong>Fictional sample results.</strong> These companies and signals are demo data. Start a new live ZoomInfo search for real leads.</div>}
            {active?.result.plan && <details className="applied-filters"><summary>Search topics and applied filters</summary><dl><dt>Source</dt><dd>{active.mock ? "Fictional sample" : "ZoomInfo Intent Search"}</dd><dt>Intent topics</dt><dd>{active.result.plan.intent_topics?.join(", ") || "Not recorded"}</dd><dt>Country</dt><dd>{active.result.plan.country || "Worldwide"}</dd>{Object.entries(active.result.plan.intent_filters || {}).map(([key, value]) => <div className="filter-detail-pair" key={key}><dt>{({ signalScoreMin: "Minimum signal score", signalScoreMax: "Maximum signal score", signalStartDate: "Signal start date", signalEndDate: "Signal end date", audienceStrengthMin: "Minimum audience", audienceStrengthMax: "Maximum audience", state: "State or province", metroRegion: "Metro region", industryCodes: "Industry codes", employeeCount: "Employee count", revenue: "Revenue range", techAttributeTagList: "Technology products" } as Record<string, string>)[key] || key}</dt><dd>{String(value)}</dd></div>)}</dl></details>}
            <div className="results-controls"><label className="filter-search"><Search size={15} /><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search companies or contacts" aria-label="Filter companies" /></label><div className="segmented">{["All", "Hot", "Warm", "Cool"].map(t => <button className={tier === t ? "active" : ""} onClick={() => setTier(t)} key={t}>{t}</button>)}</div><select aria-label="Selected search" value={selected || ""} disabled={sending} onChange={e => setSelected(e.target.value)}><option value="" disabled>Select a search</option>{runs.map(run => <option key={run.id} value={run.id}>{run.mock ? "SAMPLE" : "LIVE"} · {dateLabel(run.created_at)} · {run.query.slice(0, 42)}</option>)}</select>{view === "outreach" && <select aria-label="Email status" value={status} onChange={e => setStatus(e.target.value)}>{["all", "draft", "ready", "sent", "failed", "unknown"].map(s => <option value={s} key={s}>{s === "all" ? "All email statuses" : s[0].toUpperCase() + s.slice(1)}</option>)}</select>}</div>
            {delivery && <div className="delivery-results" role="status"><strong>{delivery.sent_count} email{delivery.sent_count !== 1 ? "s" : ""} accepted by Microsoft Graph.</strong>{delivery.results.filter(r => r.status !== "sent").map(result => <p key={result.email_id}>{drafts.find(e => e.id === result.email_id)?.to_email}: {result.detail}</p>)}</div>}
            {loading ? <div className="empty-state"><Loader2 className="spin" size={28} /><h3>Loading your results…</h3></div> : running ? <div className="run-progress"><span className="icon-tile"><Loader2 className="spin" size={24} /></span><h3>Finding your next conversation.</h3><p>Researching companies and preparing drafts. You can leave this page and return to the search.</p><ol>{steps.map(step => { const done = active?.result.progress?.some(p => p.startsWith(step + ":")); return <li key={step} className={done ? "done" : ""}><span>{done ? <Check size={12} /> : <span className="step-dot" />}</span>{names[step]}</li>; })}</ol></div> : active?.status === "failed" ? <div className="empty-state"><CircleHelp size={32} /><h3>This search couldn’t finish</h3><p>{active.error}</p></div> : !leads.length ? <div className="empty-state"><span className="empty-graphic"><Building2 size={28} /><Search size={20} /></span><h3>{active ? "No companies matched this search" : "Your next opportunity is waiting"}</h3><p>{active ? "Try a broader training topic or a lower minimum tier." : "Search live ZoomInfo signals above to find companies and prepare email drafts."}</p>{view === "outreach" && <button className="secondary" onClick={() => setView("leads")}>Find companies<ArrowRight size={15} /></button>}</div> : <>
              <div className="table-scroll"><table><thead><tr><th>Company</th><th>{view === "leads" ? "Intent signal" : "Email subject"}</th><th>Contact</th><th>Lead score</th><th>Email status</th><th><span className="sr-only">Review</span></th></tr></thead><tbody>{visibleLeads.map(lead => { const email = emailsByLead.get(lead.id); const contact = lead.contacts.find(c => c.email) || lead.contacts[0]; const signal = [...lead.signals].sort((a, b) => b.signal_score - a.signal_score)[0]; return <tr key={lead.id}><td><button className="company-button" onClick={() => setOpened(lead.id)} disabled={sending}><span className="company-icon">{company(lead.company_name).slice(0, 2).toUpperCase()}</span><span><strong>{company(lead.company_name)}</strong><small>{lead.website || "Website not available"}</small></span></button></td><td>{view === "leads" ? <><span className="topic">{signal?.topic || "Not provided"}</span><small>{signal?.signal_date || "No signal date"}{lead.signals.length > 1 && ` · +${lead.signals.length - 1} signals`}</small></> : <span className="subject-preview">{email?.subject}</span>}</td><td><strong className="contact-name">{contact ? `${contact.first_name} ${contact.last_name}` : "Not found"}</strong><small>{contact?.job_title || "No contact role"}</small></td><td><div className="score-cell"><span className={`badge ${lead.tier.toLowerCase()}`}>{lead.tier}</span><strong>{lead.score}</strong></div></td><td><span className={`status-label ${email?.status || "none"}`}><span className="dot" />{email?.status === "ready" ? "Ready to send" : email ? email.status[0].toUpperCase() + email.status.slice(1) : "No draft"}</span></td><td><button className="review-button" onClick={() => setOpened(lead.id)} disabled={sending}>Review<ChevronRight size={14} /></button></td></tr>; })}</tbody></table></div>{!visibleLeads.length && <div className="empty-state compact"><h3>No results match your filters</h3><button className="text-button" onClick={() => { setFilter(""); setTier("All"); setStatus("all"); }}>Clear filters</button></div>}
              <div className="table-footer"><span>Showing {visibleLeads.length} of {leads.length} companies</span><span><ShieldCheck size={13} />All new drafts start with approval off</span></div>
            </>}
          </section>
          {!!active?.result.errors?.length && <details className="warnings"><summary>{active.result.errors.length} search warning{active.result.errors.length > 1 ? "s" : ""}</summary>{active.result.errors.map((warning, i) => <p key={i}>{warning}</p>)}</details>}
          <div className="evidence-footnote"><CircleHelp size={15} /><p>Intent signals suggest research interest. Confirm training needs, team size and budget in conversation.</p></div>
        </>}
        {view === "history" && <><div className="page-heading"><div><span className="eyebrow">PICK UP WHERE YOU LEFT OFF</span><h1>Search history</h1><p>Your searches, evidence and email drafts, saved in one place.</p></div><button className="secondary" onClick={() => refreshRuns().catch(e => setError(e.message))}><RefreshCw size={15} />Refresh</button></div><section className="panel history-list">{runs.length ? runs.map(run => <button key={run.id} className="history-row" disabled={sending} onClick={() => { setSelected(run.id); setView("leads"); }}><span className="icon-tile"><Clock3 size={20} /></span><div><strong>{run.query}</strong><small>{dateLabel(run.created_at)} · {run.mock ? "Sample" : "Live"} · {run.lead_count} companies · {run.sent_count} sent</small></div><span className={`badge ${run.status}`}>{run.status}</span><ChevronRight size={18} /></button>) : <div className="empty-state"><History size={34} /><h3>No searches yet</h3><p>Start a search from Lead discovery.</p></div>}</section>{runs.length < runsTotal && <button className="secondary load-more" onClick={loadMoreRuns}>Load more searches</button>}</>}
        {view === "knowledge" && <KnowledgePanel />}
        {view === "settings" && <><div className="page-heading"><div><span className="eyebrow">YOUR WORKSPACE CONNECTIONS</span><h1>Ready for the real world.</h1><p>Check the services used for your research and outreach.</p></div><button className="secondary" onClick={() => Promise.all([api<Settings>("settings").then(setSettings), refreshMailbox()]).catch(e => setError(e.message))}><RefreshCw size={15} />Refresh</button></div><div className="connections-grid">{[
          [Search, "ZoomInfo", settings?.zoominfo_configured, "Live company intent and decision-maker contacts.", "ZOOMINFO_CLIENT_ID + ZOOMINFO_CLIENT_SECRET"],
          [Sparkles, "Email intelligence", settings?.llm_configured, `Provider: ${settings?.llm_provider || "none"}. Templates are used when no LLM is configured.`, "GEMINI_API_KEY or ANTHROPIC_API_KEY"],
        ].map(([Icon, label, connected, description, env]) => { const ConnectionIcon = Icon as typeof Mail; return <section className="panel connection-card" key={label as string}><div className="row between"><span className="icon-tile"><ConnectionIcon size={23} /></span><span className={`badge ${connected ? "ready" : "draft"}`}>{connected ? "Configured" : "Not configured"}</span></div><h2>{label as string}</h2><p>{description as string}</p><code>{env as string}</code>{label === "ZoomInfo" && <ZoomInfoConnection configured={!!settings?.zoominfo_configured} />}</section>; })}<MicrosoftConnection mailbox={mailbox} configured={!!settings?.microsoft_configured} disabled={sending} onChange={async () => { await refreshMailbox(); await refresh(); }} /></div><section className="panel connection-help"><ShieldCheck size={24} /><div><h2>Configure once. Review every time.</h2><p>Add the values above to the project’s backend <code>.env</code> file and restart the backend. “Configured” means credentials are present. Use Test ZoomInfo connection to verify authentication and available topics.</p><p>The Next.js frontend reads <code>BACKEND_URL</code> and <code>API_KEY</code> from <code>frontend/.env.local</code>. Provider keys stay on the server.</p><p>Opt-outs: add one email address per line to <code>data/suppression.txt</code>. Replies are not monitored automatically.</p><p>Use a Standard App with Client Credentials and an integration user. Access tokens renew automatically using their actual expiry. If the app secret itself is revoked or expires, update it in the backend configuration.</p><p>The workspace opens without login. Connect a Microsoft mailbox only when you want to send email. Email tokens renew automatically; Microsoft may occasionally ask you to reconnect or grant consent.</p></div></section></>}
        <footer className="page-footer"><span>Workforce Training & Development</span><span>Better connections. Brighter futures.</span></footer>
      </main>
    </div>
    {openLead && <EmailEditor key={openLead.id} lead={openLead} email={emailsByLead.get(openLead.id)} mock={active?.mock ?? true} mailbox={mailbox} onConnect={() => { setOpened(null); setView("settings"); }} onClose={() => setOpened(null)} onChange={refresh} />}
  </div>;
}
