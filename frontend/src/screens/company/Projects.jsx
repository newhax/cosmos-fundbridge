// Company: incoming requests (accept / decline), NGO projects ranked for this company, its calls for
// proposals (RFP), and the projects it funds.
import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../../api.js";
import { AreaPicker, StateSelect, areaLabel, toPayload } from "../../components/geo.jsx";
import { Pager } from "../../components/shell.jsx";
import { CauseChips, Demo, Err, Ok } from "../../components/ui.jsx";
import { Active, SectionTabs } from "../Projects.jsx";

const SECTIONS = [["requests", "Requests"], ["browse", "NGO projects"], ["calls", "My calls"], ["active", "Active projects"]];

export default function CompanyProjects({ status }) {
  const [section, setSection] = useState("requests");
  return (
    <div className="space-y-4">
      <SectionTabs value={section} onChange={setSection} items={SECTIONS} />
      {section === "requests" && <Requests />}
      {section === "browse" && <Browse status={status} />}
      {section === "calls" && <Calls status={status} />}
      {section === "active" && <Active role="company" />}
    </div>
  );
}

function Requests() {
  const [list, setList] = useState([]);
  const [notes, setNotes] = useState({});
  const [err, setErr] = useState("");
  const load = () => api.companyRequests().then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const decide = async (r, accept) => {
    setErr("");
    if (!accept && !window.confirm(`Decline “${r.project_title}”? The NGO is told.`)) return;
    try { await api.decideRequest(r.id, accept, notes[r.id] || ""); load(); } catch (e) { setErr(e.message); }
  };
  const pending = list.filter((r) => r.status === "pending");
  return (
    <div className="space-y-3">
      <Err msg={err} />
      {pending.length === 0 && <p className="card text-sm text-ink-500">No requests waiting for you.</p>}
      {pending.map((r) => (
        <article key={r.id} className="card space-y-2 text-sm">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div><h3 className="font-semibold">{r.project_title}</h3>
              <p className="text-xs text-ink-500">{r.ngo_name}<Demo on={r.ngo_is_demo} /> · {r.kind === "rff" ? "request for funds" : `proposal to “${r.call_title}”`} · {fmtDate(r.created_at)}</p></div>
            <span className="badge bg-ink-900 text-white">{inr(r.amount_inr)}</span>
          </div>
          {r.message && <p className="whitespace-pre-line text-xs text-ink-700">{r.message}</p>}
          <label className="label" htmlFor={`note-${r.id}`}>Note to the NGO (optional)</label>
          <input id={`note-${r.id}`} className="input" value={notes[r.id] || ""} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} />
          <div className="flex gap-2">
            <button className="btn-primary min-h-[36px] text-xs" onClick={() => decide(r, true)}>Accept</button>
            <button className="btn-ghost min-h-[36px] text-xs" onClick={() => decide(r, false)}>Decline</button>
          </div>
        </article>
      ))}
      {list.length > pending.length && (
        <details className="card text-sm"><summary className="cursor-pointer font-semibold">Decided ({list.length - pending.length})</summary>
          <ul className="mt-2 space-y-1 text-xs">{list.filter((r) => r.status !== "pending").map((r) => (
            <li key={r.id}>{r.project_title} · {r.ngo_name} · <b>{r.status}</b> {r.decided_at ? `(${fmtDate(r.decided_at)})` : ""}</li>))}</ul>
        </details>
      )}
    </div>
  );
}

function Browse({ status }) {
  const [f, setF] = useState({ sector: "", state: "" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  useEffect(() => { api.browseProjects(f, page).then(setData).catch(() => setData({ items: [], pages: 1, page: 1 })); }, [f, page]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="bp-sector">Sector</label>
        <select id="bp-sector" className="input w-48" value={f.sector} onChange={(e) => { setPage(1); setF({ ...f, sector: e.target.value }); }}>
          <option value="">All sectors</option>{(status?.sectors || []).map((s) => <option key={s}>{s}</option>)}</select>
        <label className="sr-only" htmlFor="bp-state">State</label>
        <StateSelect id="bp-state" className="input w-56" allowAll value={f.state} onChange={(v) => { setPage(1); setF({ ...f, state: v }); }} />
      </div>
      <p className="text-xs text-ink-500">Ranked for you: sectors 45%, location 35% (same district &gt; same state &gt; pan-India), budget 20%. Missing data counts as unknown.</p>
      <div className="grid gap-3 md:grid-cols-2">
        {data?.items.length === 0 && <p className="text-sm text-ink-500">No open projects match.</p>}
        {(data?.items || []).map((p) => (
          <article key={p.id} className="card space-y-2 text-sm">
            <div className="flex items-start justify-between gap-2">
              <div><h3 className="font-semibold">{p.title}</h3><p className="text-xs text-ink-500">{p.ngo_name}<Demo on={p.ngo_is_demo} /></p></div>
              <span className="badge bg-ink-900 text-white" title="Fit with your profile (0-100)">{p.fit.total} fit</span>
            </div>
            <p className="text-xs text-ink-500">{p.areas.map((a) => areaLabel(a)).join(" · ")} · {p.sectors.join(", ")} · budget {inr(p.budget_inr)}</p>
            <p className="text-xs text-ink-700">{p.summary}</p>
            <ul className="text-xs text-ink-600">{p.fit.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
            {p.fit.unknowns.length > 0 && <p className="text-[11px] text-ink-400">Unknown: {p.fit.unknowns.join("; ")}</p>}
            {p.request_status && <p className="text-xs">This NGO already sent you a request: <b>{p.request_status}</b></p>}
          </article>
        ))}
      </div>
      {data && <Pager page={data.page} pages={data.pages} onPage={setPage} />}
    </div>
  );
}

const EMPTY = { title: "", sectors: [], description: "", budget_min: "", budget_max: "", deadline: "", areas: [] };

function Calls({ status }) {
  const [list, setList] = useState([]);
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.myCalls().then(setList).catch(() => {});
  useEffect(() => { load(); }, []);
  const create = async (e) => {
    e.preventDefault(); setErr(""); setMsg("");
    try {
      await api.createCall({ ...f, areas: toPayload(f.areas), budget_min: f.budget_min || null, budget_max: f.budget_max || null });
      setF(EMPTY); setMsg("Call published. NGOs can now send proposals."); load();
    } catch (x) { setErr(x.message); }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
      <form onSubmit={create} className="card space-y-3 text-sm">
        <h3 className="font-semibold">New call for proposals (RFP)</h3>
        <div><label className="label" htmlFor="cl-title">Title</label><input id="cl-title" className="input" required minLength={5} maxLength={120} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div><span className="label">Sectors</span><CauseChips options={status?.sectors || []} value={f.sectors} onChange={(v) => setF({ ...f, sectors: v })} /></div>
        <div><span className="label">Focus areas (optional)</span><AreaPicker value={f.areas} onChange={(v) => setF({ ...f, areas: v })} idPrefix="cl" /></div>
        <div><label className="label" htmlFor="cl-desc">What you are looking for</label><textarea id="cl-desc" className="input" rows={4} required minLength={20} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div><label className="label" htmlFor="cl-min">Min (INR)</label><input id="cl-min" className="input" type="number" min={1} value={f.budget_min} onChange={(e) => setF({ ...f, budget_min: e.target.value })} /></div>
          <div><label className="label" htmlFor="cl-max">Max (INR)</label><input id="cl-max" className="input" type="number" min={1} value={f.budget_max} onChange={(e) => setF({ ...f, budget_max: e.target.value })} /></div>
          <div><label className="label" htmlFor="cl-dl">Deadline</label><input id="cl-dl" className="input" type="date" value={f.deadline} onChange={(e) => setF({ ...f, deadline: e.target.value })} /></div>
        </div>
        <Err msg={err} /><Ok msg={msg} />
        <button className="btn-primary">Publish call</button>
      </form>
      <div className="space-y-3">
        {list.length === 0 && <p className="card text-sm text-ink-500">No calls yet.</p>}
        {list.map((c) => (
          <article key={c.id} className="card space-y-1 text-sm">
            <div className="flex items-start justify-between gap-2"><h3 className="font-semibold">{c.title}</h3>
              <span className={`badge ${c.is_open ? "bg-sage-100 text-sage-900" : "bg-ink-100 text-ink-600"}`}>{c.is_open ? "open" : "closed"}</span></div>
            <p className="text-xs text-ink-500">{c.sectors.join(", ")} · {c.areas.map((a) => areaLabel(a)).join(", ") || "anywhere"} · deadline {c.deadline ? fmtDate(c.deadline) : "none"}</p>
            <p className="text-xs">{c.proposals} proposal(s) · see them under Requests</p>
            {c.status === "open" && <button className="btn-ghost min-h-[32px] px-3 py-1 text-xs" onClick={() => api.closeCall(c.id).then(load)}>Close call</button>}
          </article>
        ))}
      </div>
    </div>
  );
}
