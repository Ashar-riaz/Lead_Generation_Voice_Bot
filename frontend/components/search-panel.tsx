"use client";
import { useState } from "react";
import { ArrowUpRight, Building2, MapPin, Plus, Radio, Search, SlidersHorizontal, X } from "lucide-react";
import { api, payload, type Run, type Settings } from "@/lib/api";
import FilterLookup from "./filter-lookup";

type FilterTab = "intent" | "company" | "location";

export default function SearchPanel({ settings, onCreated, onError }: { settings: Settings | null; onCreated: (run: Run) => void; onError: (message: string) => void }) {
  const [query, setQuery] = useState("Companies exploring AI training");
  const [tab, setTab] = useState<FilterTab>("intent");
  const [topics, setTopics] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [minScore, setMinScore] = useState(70);
  const [maxScore, setMaxScore] = useState(100);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [audienceMin, setAudienceMin] = useState("");
  const [audienceMax, setAudienceMax] = useState("");
  const [industry, setIndustry] = useState("");
  const [employees, setEmployees] = useState("");
  const [revenue, setRevenue] = useState("");
  const [tech, setTech] = useState("");
  const [country, setCountry] = useState<string | null>(null);
  const [state, setState] = useState("");
  const [metro, setMetro] = useState("");
  const [limit, setLimit] = useState(10);
  const [tier, setTier] = useState("Cool");
  const [enrich, setEnrich] = useState(true);
  const [writeEmails, setWriteEmails] = useState(true);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const liveReady = !!settings?.zoominfo_configured;
  const lookupDisabled = !liveReady;
  const effectiveCountry = country ?? settings?.default_country ?? "";
  function addTopic() {
    const name = topic.trim();
    if (name && topics.length < 50 && !topics.some(t => t.toLowerCase() === name.toLowerCase())) setTopics([...topics, name]);
    setTopic("");
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (topic.trim()) { onError("Add the typed intent topic with the + button, or clear it before searching."); return; }
    if (minScore > maxScore) { onError("Minimum signal score cannot exceed maximum signal score."); return; }
    if (start && end && start > end) { onError("Signal start date cannot be after the end date."); return; }
    if (audienceMin && audienceMax && audienceMin < audienceMax) { onError("Minimum audience strength cannot exceed maximum strength. A is strongest; E is weakest."); return; }
    setBusy(true);
    try {
      onCreated(await api<Run>("runs", payload("POST", {
        query, limit, country: effectiveCountry.trim(), min_tier: tier, enrich, write_emails: writeEmails,
        intent_topics: topics, min_signal_score: minScore, max_signal_score: maxScore,
        signal_start_date: start || null, signal_end_date: end || null,
        audience_strength_min: audienceMin || null, audience_strength_max: audienceMax || null,
        industry_codes: industry, employee_count: employees, revenue, tech_products: tech, state, metro_region: metro,
      })));
    } catch (error) { onError((error as Error).message); } finally { setBusy(false); }
  }
  return <section className="search-card">
    <div className="section-heading"><div className="row gap"><span className="icon-tile"><Radio size={19} /></span><div><h2>Signals · Intent</h2><p>Find companies researching topics relevant to your training.</p></div></div>
      <span className="live-source"><span className="dot green" />Live ZoomInfo</span></div>
    {settings && !liveReady && <div className="live-setup" role="status"><strong>Connect ZoomInfo to search real companies.</strong><p>Add your Standard App client ID and client secret to the backend configuration, restart it, and check Connections.</p></div>}
    <form onSubmit={submit}>
      <div className="search-field"><Search size={20} /><input aria-label="Describe your lead search" value={query} onChange={e => setQuery(e.target.value)} minLength={3} maxLength={1000} required /><button className="primary" disabled={busy || !liveReady}>{busy ? "Starting…" : "Find companies"}<ArrowUpRight size={18} /></button></div>
      <div className="search-bottom"><div className="suggestions"><span>Try</span>{["AI training", "Cyber security", "Data skills"].map(q => <button type="button" className="chip" key={q} onClick={() => setQuery(`Companies exploring ${q.toLowerCase()}`)}>{q}</button>)}</div><button className="text-button" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal size={14} />Search options</button></div>
      <div className="signal-filters">
        <div className="filter-tabs" role="tablist" aria-label="Signal filters">{([
          ["intent", Radio, "Intent"], ["company", Building2, "Company"], ["location", MapPin, "Location"],
        ] as const).map(([key, Icon, label]) => <button key={key} type="button" role="tab" id={`tab-${key}`} aria-selected={tab === key} aria-controls={`filters-${key}`} className={tab === key ? "active" : ""} onClick={() => setTab(key)}><Icon size={14} />{label}</button>)}</div>
        <div role="tabpanel" id={`filters-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "intent" && <>
            <div className="topic-control"><FilterLookup label="Add intent topic" field="intent-topics" value={topic} onChange={setTopic} useName disabled={lookupDisabled} placeholder="Choose an exact ZoomInfo topic" /><button type="button" className="secondary" aria-label="Add topic" onClick={addTopic} disabled={!topic.trim() || topics.length >= 50}><Plus size={16} />Add</button></div>
            {!!topics.length && <div className="topic-chips">{topics.map(t => <span key={t}>{t}<button type="button" className="icon-button" aria-label={`Remove ${t}`} onClick={() => setTopics(topics.filter(value => value !== t))}><X size={12} /></button></span>)}</div>}
            <p className="field-hint">{topics.length ? `${topics.length} of 50 topics selected. Only these topics will be searched.` : "Leave topics empty to choose matching ZoomInfo topics from your description."}</p>
            <div className="filter-grid four-columns">
              <label>Minimum signal score<input type="number" min={60} max={100} value={minScore} onChange={e => setMinScore(Number(e.target.value))} required /></label>
              <label>Maximum signal score<input type="number" min={60} max={100} value={maxScore} onChange={e => setMaxScore(Number(e.target.value))} required /></label>
              <label>Signal start date<input type="date" value={start} max={end || undefined} onChange={e => setStart(e.target.value)} /></label>
              <label>Signal end date<input type="date" value={end} min={start || undefined} onChange={e => setEnd(e.target.value)} /></label>
              <label>Minimum audience strength<select value={audienceMin} onChange={e => setAudienceMin(e.target.value)}><option value="">Any</option>{["E", "D", "C", "B", "A"].map(v => <option key={v} value={v}>{v}{v === "A" ? " · strongest" : v === "E" ? " · weakest" : ""}</option>)}</select></label>
              <label>Maximum audience strength<select value={audienceMax} onChange={e => setAudienceMax(e.target.value)}><option value="">Any</option>{["A", "B", "C", "D", "E"].map(v => <option key={v} value={v}>{v}{v === "A" ? " · strongest" : v === "E" ? " · weakest" : ""}</option>)}</select></label>
            </div>
          </>}
          {tab === "company" && <><p className="field-hint">Choose lookup values or enter comma-separated codes. Empty fields include all companies.</p><div className="filter-grid">
            <FilterLookup label="Industry" field="industries" value={industry} onChange={setIndustry} disabled={!liveReady} />
            <FilterLookup label="Employee count" field="employee-count" value={employees} onChange={setEmployees} disabled={!liveReady} placeholder="e.g. 100to249" />
            <FilterLookup label="Revenue range" field="revenue-ranges" value={revenue} onChange={setRevenue} disabled={!liveReady} />
            <FilterLookup label="Technology products" field="tech-products" value={tech} onChange={setTech} disabled={!liveReady} />
          </div></>}
          {tab === "location" && <><p className="field-hint">Filter by company location. Clear country to search worldwide.</p><div className="filter-grid">
            <FilterLookup label="Country" field="countries" value={effectiveCountry} onChange={setCountry} useName disabled={lookupDisabled} placeholder="Worldwide" />
            <FilterLookup label="State or province" field="states" value={state} onChange={setState} useName disabled={!liveReady} />
            <FilterLookup label="Metro region" field="metro-regions" value={metro} onChange={setMetro} useName disabled={!liveReady} />
          </div></>}
        </div>
      </div>
      {advanced && <div className="advanced"><label>Maximum companies<input type="number" min={1} max={50} value={limit} onChange={e => setLimit(Number(e.target.value))} /></label><label>Minimum tier<select value={tier} onChange={e => setTier(e.target.value)}><option value="Cool">All tiers</option><option>Warm</option><option>Hot</option></select></label><label className="checkbox-label"><input type="checkbox" checked={enrich} onChange={e => setEnrich(e.target.checked)} />Find contact emails</label><label className="checkbox-label"><input type="checkbox" checked={writeEmails} onChange={e => setWriteEmails(e.target.checked)} />Create email drafts</label></div>}
    </form>
    <div className="search-note"><span className="dot green" />LIVE ZOOMINFO · {effectiveCountry || "Worldwide"}. Contact enrichment may use account credits.</div>
  </section>;
}
