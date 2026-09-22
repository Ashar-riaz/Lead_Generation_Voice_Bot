"use client";
import { useId, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

type Row = { id: string; attributes?: { name?: string; value?: string; code?: string } };

export default function FilterLookup({ label, field, value, onChange, disabled = false, useName = false, placeholder = "Any" }: {
  label: string; field: string; value: string; onChange: (value: string) => void;
  disabled?: boolean; useName?: boolean; placeholder?: string;
}) {
  const id = useId();
  const [rows, setRows] = useState<Row[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load(refresh = false) {
    if (disabled || busy) return;
    setBusy(true); setError("");
    try {
      const result = await api<{ items: Row[] }>(`lookups/${field}?refresh=${refresh}`);
      setRows(result.items); setLoaded(true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="lookup-field">
    <label htmlFor={id}>{label}</label>
    <div className="lookup-input"><input id={id} list={`${id}-values`} value={value} disabled={disabled} maxLength={field === "intent-topics" ? 200 : 500}
      placeholder={busy ? "Loading values…" : placeholder} onFocus={() => { if (!loaded) void load(); }} onChange={e => onChange(e.target.value)} />
      <button type="button" className="icon-button" aria-label={`Reload ${label.toLowerCase()}`} disabled={disabled || busy} onClick={() => void load(true)}><RefreshCw size={14} className={busy ? "spin" : ""} /></button></div>
    <datalist id={`${id}-values`}>{rows.map((row, i) => {
      const attributes = row.attributes || {};
      const optionValue = useName ? attributes.name || row.id : attributes.value ?? attributes.code ?? row.id;
      return <option key={`${row.id}-${i}`} value={String(optionValue)}>{attributes.name || row.id}</option>;
    })}</datalist>
    {error && <p className="field-error" role="alert">{error}</p>}
    {loaded && !rows.length && <p className="field-hint">No lookup values returned for this account.</p>}
  </div>;
}
