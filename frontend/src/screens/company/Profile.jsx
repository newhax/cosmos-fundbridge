import { useEffect, useState } from "react";
import { api, inr } from "../../api.js";
import { AreaPicker, toPayload } from "../../components/geo.jsx";
import { SourceChip } from "../../components/Sources.jsx";
import { CauseChips, Err, Field, Ok } from "../../components/ui.jsx";
import VerificationBadge from "../../components/VerificationBadge.jsx";

export default function CompanyProfile({ status, onSaved }) {
  const [f, setF] = useState(null);
  const [fp, setFp] = useState(null);
  const [ver, setVer] = useState(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    api.companyProfile().then(setF).catch((e) => setErr(e.message));
    api.footprint().then(setFp).catch(() => {});
    api.myVerification().then(setVer).catch(() => {});
  };
  useEffect(load, []);
  if (!f) return <p className="text-slate-500">Loading…</p>;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const save = async () => {
    setErr(""); setMsg("");
    try {
      const { name, cin, pan, csr_contact_email, focus_sectors, min_ticket, max_ticket } = f;
      setF(await api.saveCompanyProfile({ name, cin, pan, csr_contact_email, focus_sectors, min_ticket, max_ticket,
        hq_area: f.hq_area ? toPayload([f.hq_area])[0] : [], operating_areas: toPayload(f.operating_areas),
        focus_areas: toPayload(f.focus_areas) }));
      setMsg("Saved.");
      load(); onSaved?.();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="card space-y-3">
        <h2 className="text-lg font-semibold">Company profile</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Company name"><input className="input" value={f.name} onChange={set("name")} /></Field>
          <Field label="CIN" hint="Used to find your CSR filings"><input className="input" value={f.cin || ""} onChange={set("cin")} /></Field>
          <Field label="PAN (optional)"><input className="input" value={f.pan || ""} onChange={set("pan")} /></Field>
          <Field label="CSR contact email" hint="Private. Not shown to NGOs."><input className="input" value={f.csr_contact_email || ""} onChange={set("csr_contact_email")} /></Field>
        </div>
        <Field label="Focus sectors (used by the Screener)"><CauseChips options={status?.sectors || []} value={f.focus_sectors} onChange={(v) => setF({ ...f, focus_sectors: v })} placeholder="e.g. mental health, menstrual hygiene" /></Field>
        <Field label="Head office" hint="One state/UT, optionally a district">
          <AreaPicker single allowAllIndia={false} idPrefix="cho" value={f.hq_area ? [f.hq_area] : []} onChange={(v) => setF({ ...f, hq_area: v[0] || null })} /></Field>
        <Field label="Where you operate (self-declared, optional)">
          <AreaPicker idPrefix="cop" value={f.operating_areas || []} onChange={(v) => setF({ ...f, operating_areas: v })} /></Field>
        <Field label="CSR focus areas (used for matching)" hint="District match counts most, then state, then pan-India">
          <AreaPicker idPrefix="cfa" value={f.focus_areas || []} onChange={(v) => setF({ ...f, focus_areas: v })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Min ticket (INR)"><input type="number" className="input" value={f.min_ticket ?? ""} onChange={set("min_ticket")} /></Field>
          <Field label="Max ticket (INR)"><input type="number" className="input" value={f.max_ticket ?? ""} onChange={set("max_ticket")} /></Field>
        </div>
        <Err msg={err} /><Ok msg={msg} />
        <button className="btn-primary" onClick={save}>Save</button>
        {ver && <div className="border-t pt-2"><VerificationBadge v={ver} detail />
          <ul className="mt-1 text-[11px] text-slate-600">{ver.auto_checks.map((a) => <li key={a.check}>{a.ok ? "✓" : "✗"} {a.detail}</li>)}</ul></div>}
      </div>
      <div className="card text-sm">
        <h2 className="mb-2 text-lg font-semibold">Your reported CSR footprint</h2>
        {!fp && <p className="text-slate-500">Loading…</p>}
        {fp && !fp.found && <p className="text-slate-600">{fp.message}</p>}
        {fp?.found && (
          <>
            <p className="text-xs text-slate-500">From public filings ({fp.data_source}) for CIN {fp.cin}, as filed under "{fp.company_name}".
              <SourceChip refs={fp.source_refs} title="Your filing rows" /></p>
            <table className="mt-2 w-full text-xs">
              <thead className="text-slate-400"><tr><th className="text-left">FY</th><th className="text-left">Sector</th><th className="text-right">Amount</th></tr></thead>
              <tbody>{fp.spend_by_sector_year.map((r, i) => (
                <tr key={i} className="border-t"><td>{r.fy}</td><td>{r.sector}</td><td className="text-right">{inr(r.amount_inr)}</td></tr>))}</tbody>
            </table>
            <h4 className="mt-3 text-xs font-semibold uppercase text-slate-500">Data caveats and unknowns</h4>
            <ul className="list-disc pl-5 text-xs text-slate-600">{[...fp.caveats, ...fp.unknowns].map((c) => <li key={c}>{c}</li>)}</ul>
          </>
        )}
      </div>
    </div>
  );
}
