import { useState } from "react";
import { api } from "../api.js";
import { useDistricts, useStates } from "../components/geo.jsx";
import { CauseChips } from "../components/ui.jsx";

export const DEMO_RUN = {
  cause: "Education for children in rural Rajasthan",
  state: "Rajasthan",
  district: "Barmer",
  project_summary: "We run community libraries and after-school reading circles in 12 village government schools in Barmer, with trained local women as reading mentors.",
  funding_ask_inr: 1500000,
  sectors: [],
};

const EMPTY = { cause: "", state: "Rajasthan", district: "", project_summary: "", funding_ask_inr: 500000, sectors: [] };

export default function Onboarding({ status, onRun, current, me }) {
  const prof = me?.profile || {};
  const [form, setForm] = useState(current ? { ...EMPTY, ...current, sectors: [] }
    : { ...EMPTY, state: prof.state || EMPTY.state, district: prof.district || "", project_summary: prof.summary || "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const { cause, state, district, project_summary, funding_ask_inr, sectors } = form;
      onRun(await api.run({ cause, state, district, project_summary, sectors, funding_ask_inr: Number(funding_ask_inr) }));
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <form onSubmit={submit} className="card space-y-3 lg:col-span-2">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Find CSR funders for {prof.name || "your NGO"}</h2>
          <button type="button" className="btn-ghost" onClick={() => setForm(DEMO_RUN)}>Load demo project</button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2"><label className="label" htmlFor="ob-cause">Cause</label><input id="ob-cause" required className="input" value={form.cause} onChange={set("cause")} placeholder="e.g. girls' education" /></div>
          <PlaceFields form={form} setForm={setForm} />
        </div>
        <div><label className="label" htmlFor="ob-summary">Project summary</label>
          <textarea id="ob-summary" className="input h-24" value={form.project_summary} onChange={set("project_summary")} /></div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className="label" htmlFor="ob-ask">Funding ask (INR)</label>
            <input id="ob-ask" type="number" min="1" required className="input" value={form.funding_ask_inr} onChange={set("funding_ask_inr")} /></div>
        </div>
        <div>
          <label className="label">Sectors (optional: leave empty to let the Prospector map your cause; picking them yourself is fastest)</label>
          <CauseChips options={status?.sectors || []} value={form.sectors} onChange={(v) => setForm({ ...form, sectors: v })}
            placeholder="e.g. mental health, menstrual hygiene"
            hint="Public CSR filings only use the sectors above, so a sector you type is searched as the closest of them; the ranking shows how it was matched." />
        </div>
        <p className="rounded-box bg-ink-100 px-3 py-2 text-xs text-ink-600">
          Demo mode: do not enter confidential data. Only public CSR data and these NGO details are sent to the model providers.
        </p>
        {err && <p className="text-sm text-red-700">{err}</p>}
        <button className="btn-primary" disabled={busy}>{busy ? "Running Prospector → Researcher…" : "Find funders"}</button>
      </form>
      <DataQuality report={status?.data_quality} />
    </div>
  );
}

function DataQuality({ report }) {
  if (!report) return <div className="card text-sm text-slate-500">Loading data-quality report…</div>;
  const rows = [
    ["Rows read", report.rows_read],
    ["Exact duplicates removed", report.exact_duplicates_removed],
    ["Rows with missing state", report.rows_missing_state],
    ["Rows with multiple states / PAN India", report.rows_multiple_states],
    ["Company-name variants merged", report.name_variants_merged],
    ["Companies after merging", report.companies],
    ["Rows with unmapped sector (\"other\")", report.rows_sector_other_or_unmapped],
    ["Rows without CIN", report.rows_missing_cin],
    ["Rows dropped (bad amount / year)", (report.rows_dropped_invalid_amount || 0) + (report.rows_dropped_unparseable_year || 0)],
    ["Rows loaded", report.rows_loaded],
  ];
  return (
    <div className="card text-sm">
      <h3 className="mb-1 font-semibold">Data-quality report</h3>
      <p className="mb-2 text-xs text-slate-500">
        {report.data_source} · {report.source_files?.join(", ")} · FY {report.fy_range?.join(" to ")}
      </p>
      <table className="w-full">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b last:border-0"><td className="py-1 text-slate-600">{k}</td><td className="text-right font-mono">{v ?? 0}</td></tr>
          ))}
        </tbody>
      </table>
      {report.name_merge_examples?.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-600">Example name merges</summary>
          {report.name_merge_examples.map((m) => (
            <div key={m.company_id} className="mt-1"><span className="font-mono text-slate-400">{m.company_id}</span>: {m.variants.join(" | ")}</div>
          ))}
        </details>
      )}
      <p className="mt-2 text-[11px] text-slate-400">Rows with missing or multiple states are kept but never attributed to a single state.</p>
    </div>
  );
}

// State and district from the official LGD list (every state and UT); names are sent and resolved strictly by the server.
function PlaceFields({ form, setForm }) {
  const states = useStates();
  const code = states.find((s) => s.name === form.state)?.lgd_code || "";
  const districts = useDistricts(code);
  return (
    <>
      <div>
        <label className="label" htmlFor="ob-state">State / UT</label>
        <select id="ob-state" className="input" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value, district: "" })}>
          {!states.some((s) => s.name === form.state) && form.state && <option value={form.state}>{form.state} (not in LGD list)</option>}
          {states.map((s) => <option key={s.lgd_code} value={s.name}>{s.name}{s.kind === "ut" ? " (UT)" : ""}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="ob-district">District</label>
        <select id="ob-district" className="input" value={form.district} onChange={(e) => setForm({ ...form, district: e.target.value })}>
          <option value="">Any district (optional)</option>
          {form.district && !districts.some((d) => d.name === form.district) && <option value={form.district}>{form.district} (not in LGD list)</option>}
          {districts.map((d) => <option key={d.lgd_code} value={d.name}>{d.name}</option>)}
        </select>
      </div>
    </>
  );
}
