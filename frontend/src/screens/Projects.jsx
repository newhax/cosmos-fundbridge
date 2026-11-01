// NGO: projects (funding needs), calls for proposals from companies, requests sent, and accepted projects.
import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../api.js";
import Engagement from "../components/Engagement.jsx";
import { AreaPicker, areaLabel, toPayload } from "../components/geo.jsx";
import { Pager, ShareLinks } from "../components/shell.jsx";
import { CauseChips, Demo, Err, Ok } from "../components/ui.jsx";

const SECTIONS = [["projects", "My projects"], ["calls", "Calls for proposals"], ["requests", "Requests sent"], ["active", "Active projects"]];
const EMPTY = { title: "", sectors: [], summary: "", beneficiaries: "", budget_inr: "", duration_months: "", areas: [], status: "open" };

export default function Projects({ status }) {
  const [section, setSection] = useState("projects");
  const [projects, setProjects] = useState([]);
  const [err, setErr] = useState("");
  const loadProjects = () => api.myProjects().then(setProjects).catch((e) => setErr(e.message));
  useEffect(() => { loadProjects(); }, []);
  return (
    <div className="space-y-4">
      <SectionTabs value={section} onChange={setSection} items={SECTIONS} />
      <Err msg={err} />
      {section === "projects" && <MyProjects status={status} projects={projects} reload={loadProjects} />}
      {section === "calls" && <Calls projects={projects} />}
      {section === "requests" && <Requests />}
      {section === "active" && <Active role="ngo" />}
    </div>
  );
}

export function SectionTabs({ value, onChange, items }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1.5">
      {items.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}
          className={`rounded-full px-4 py-2 text-sm font-medium transition ${value === k ? "bg-ink-900 text-white" : "bg-white text-ink-700 ring-1 ring-inset ring-ink-900/10 hover:ring-ink-900/40"}`}>{l}</button>
      ))}
    </div>
  );
}

function MyProjects({ status, projects, reload }) {
  const [editing, setEditing] = useState(null);
  const [sendFor, setSendFor] = useState(null);
  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <div className="space-y-3">
        <div className="flex justify-between gap-2">
          <p className="text-sm text-ink-600">Companies on FundBridge can find your open projects and contact you.</p>
          <button className="btn-primary min-h-[38px] text-xs" onClick={() => setEditing({ ...EMPTY })}>New project</button>
        </div>
        {projects.length === 0 && <p className="card text-sm text-ink-500">No projects yet. Add one to list a funding need.</p>}
        {projects.map((p) => (
          <article key={p.id} className="card space-y-2 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div><h3 className="font-semibold">{p.title}<Demo on={p.is_demo} /></h3>
                <p className="text-xs text-ink-500">{p.areas.map((a) => areaLabel(a)).join(" · ") || "location unknown"}</p></div>
              <span className={`badge ${p.status === "open" ? "bg-sage-100 text-sage-900" : "bg-ink-100 text-ink-600"}`}>{p.status}</span>
            </div>
            <div className="flex flex-wrap gap-1">{p.sectors.map((s) => <span key={s} className="badge bg-ink-100 text-ink-700">{s}</span>)}</div>
            <p className="text-xs text-ink-700">{p.summary}</p>
            <p className="text-xs text-ink-500">Budget {inr(p.budget_inr)} · {p.duration_months ? `${p.duration_months} months` : "duration unknown"}
              {p.beneficiaries ? ` · ${p.beneficiaries}` : ""}</p>
            {p.requests.length > 0 && (
              <ul className="text-xs text-ink-600">{p.requests.map((r) => <li key={r.id}>→ {r.company_name}: <b>{r.status}</b> ({fmtDate(r.created_at)})</li>)}</ul>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-ghost min-h-[34px] px-3 py-1 text-xs" onClick={() => setEditing({ ...p })}>Edit</button>
              {p.status === "open" && <button className="btn-primary min-h-[34px] px-3 py-1 text-xs" onClick={() => setSendFor(p)}>Request funds</button>}
              <ShareLinks label="Share summary" text={`${p.title} (${p.sectors.join(", ")}) in ${p.areas.map((a) => areaLabel(a)).join(", ")}. Budget ${inr(p.budget_inr)}. Listed on FundBridge.`} />
            </div>
          </article>
        ))}
      </div>
      <div className="space-y-3">
        {editing && <ProjectForm status={status} value={editing} onDone={() => { setEditing(null); reload(); }} onCancel={() => setEditing(null)} />}
        {sendFor && <RequestFunds project={sendFor} onDone={() => { setSendFor(null); reload(); }} onCancel={() => setSendFor(null)} />}
      </div>
    </div>
  );
}

function ProjectForm({ status, value, onDone, onCancel }) {
  const [f, setF] = useState(value);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault(); setErr("");
    try {
      const body = { ...f, areas: toPayload(f.areas), budget_inr: f.budget_inr || null, duration_months: f.duration_months || null };
      await api.saveProject(body, f.id); onDone();
    } catch (x) { setErr(x.message); }
  };
  return (
    <form onSubmit={save} className="card space-y-3 text-sm">
      <h3 className="font-semibold">{f.id ? "Edit project" : "New project"}</h3>
      <div><label className="label" htmlFor="pf-title">Title</label><input id="pf-title" className="input" required minLength={5} maxLength={120} value={f.title} onChange={set("title")} /></div>
      <div><span className="label">Sectors</span><CauseChips options={status?.sectors || []} value={f.sectors} onChange={(v) => setF({ ...f, sectors: v })} /></div>
      <div><span className="label">Where it runs (district, whole state or all India)</span><AreaPicker value={f.areas} onChange={(v) => setF({ ...f, areas: v })} idPrefix="pf" /></div>
      <div><label className="label" htmlFor="pf-sum">What the money does</label><textarea id="pf-sum" className="input" rows={4} required minLength={20} value={f.summary} onChange={set("summary")} /></div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div><label className="label" htmlFor="pf-budget">Budget (INR)</label><input id="pf-budget" className="input" type="number" min={1} value={f.budget_inr || ""} onChange={set("budget_inr")} /></div>
        <div><label className="label" htmlFor="pf-dur">Duration (months)</label><input id="pf-dur" className="input" type="number" min={1} max={120} value={f.duration_months || ""} onChange={set("duration_months")} /></div>
      </div>
      <div><label className="label" htmlFor="pf-ben">Who benefits (optional)</label><input id="pf-ben" className="input" maxLength={300} value={f.beneficiaries || ""} onChange={set("beneficiaries")} /></div>
      {f.id && <div><label className="label" htmlFor="pf-status">Status</label>
        <select id="pf-status" className="input" value={f.status} onChange={set("status")}><option value="open">open</option><option value="funded">funded</option><option value="closed">closed</option></select></div>}
      <Err msg={err} />
      <div className="flex gap-2"><button className="btn-primary">Save</button><button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

function RequestFunds({ project, onDone, onCancel }) {
  const [companies, setCompanies] = useState([]);
  const [f, setF] = useState({ company_id: "", amount_inr: project.budget_inr || "", message: "" });
  const [err, setErr] = useState("");
  useEffect(() => { api.platformCompanies().then(setCompanies).catch(() => {}); }, []);
  const send = async (e) => {
    e.preventDefault(); setErr("");
    try { await api.requestFunds(project.id, { ...f, amount_inr: f.amount_inr || null }); onDone(); } catch (x) { setErr(x.message); }
  };
  return (
    <form onSubmit={send} className="card space-y-3 text-sm">
      <h3 className="font-semibold">Request funds: {project.title}</h3>
      <p className="text-xs text-ink-500">The company is notified and can accept or decline. Nothing is sent outside FundBridge.</p>
      <div><label className="label" htmlFor="rf-co">Company</label>
        <select id="rf-co" className="input" required value={f.company_id} onChange={(e) => setF({ ...f, company_id: e.target.value })}>
          <option value="">Choose…</option>
          {companies.map((c) => <option key={c.company_id} value={c.company_id}>{c.name}{c.focus_sectors.length ? ` · ${c.focus_sectors.slice(0, 3).join(", ")}` : ""}</option>)}
        </select></div>
      <div><label className="label" htmlFor="rf-amt">Amount asked (INR)</label><input id="rf-amt" className="input" type="number" min={1} value={f.amount_inr} onChange={(e) => setF({ ...f, amount_inr: e.target.value })} /></div>
      <div><label className="label" htmlFor="rf-msg">Message</label><textarea id="rf-msg" className="input" rows={3} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
      <Err msg={err} />
      <div className="flex gap-2"><button className="btn-primary">Send request</button><button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

function Calls({ projects }) {
  const [data, setData] = useState(null);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null);
  const [f, setF] = useState({ project_id: "", amount_inr: "", message: "" });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.openCalls(page).then(setData).catch((e) => setErr(e.message)); }, [page]);
  const send = async (e) => {
    e.preventDefault(); setErr(""); setMsg("");
    try { await api.submitProposal(open.id, { ...f, amount_inr: f.amount_inr || null }); setMsg("Proposal sent."); setOpen(null); }
    catch (x) { setErr(x.message); }
  };
  const openProjects = projects.filter((p) => p.status === "open");
  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-500">Open calls from companies, ranked by your causes and areas. Deadlines are shown as DD/MM/YYYY.</p>
      <Ok msg={msg} /><Err msg={err} />
      {data?.items.length === 0 && <p className="card text-sm text-ink-500">No open calls right now.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {(data?.items || []).map((c) => (
          <article key={c.id} className="card space-y-2 text-sm">
            <div className="flex items-start justify-between gap-2">
              <div><h3 className="font-semibold">{c.title}</h3><p className="text-xs text-ink-500">{c.company_name}<Demo on={c.company_is_demo} /></p></div>
              <span className="badge bg-ink-900 text-white">{c.fit.total} fit</span>
            </div>
            <p className="text-xs text-ink-700">{c.description}</p>
            <p className="text-xs text-ink-500">{c.sectors.join(", ")} · {c.areas.map((a) => areaLabel(a)).join(", ")} · {inr(c.budget_min)} to {inr(c.budget_max)} · deadline {c.deadline ? fmtDate(c.deadline) : "none"}</p>
            <ul className="text-xs text-ink-600">{c.fit.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
            {open?.id === c.id ? (
              <form onSubmit={send} className="space-y-2 rounded-box bg-ink-50 p-3">
                <div><label className="label" htmlFor="pp-prj">Your project</label>
                  <select id="pp-prj" className="input" required value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}>
                    <option value="">Choose…</option>{openProjects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></div>
                <div><label className="label" htmlFor="pp-amt">Amount (INR)</label><input id="pp-amt" className="input" type="number" min={1} value={f.amount_inr} onChange={(e) => setF({ ...f, amount_inr: e.target.value })} /></div>
                <div><label className="label" htmlFor="pp-msg">Message</label><textarea id="pp-msg" className="input" rows={2} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
                <div className="flex gap-2"><button className="btn-primary min-h-[36px] text-xs">Submit proposal</button><button type="button" className="btn-ghost min-h-[36px] text-xs" onClick={() => setOpen(null)}>Cancel</button></div>
              </form>
            ) : (
              <button className="btn-ghost min-h-[34px] px-3 py-1 text-xs" disabled={!openProjects.length} onClick={() => setOpen(c)}>
                {openProjects.length ? "Send a proposal" : "Add an open project first"}</button>
            )}
          </article>
        ))}
      </div>
      {data && <Pager page={data.page} pages={data.pages} onPage={setPage} />}
    </div>
  );
}

function Requests() {
  const [list, setList] = useState([]);
  const load = () => api.ngoRequests().then(setList).catch(() => {});
  useEffect(() => { load(); }, []);
  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-ink-200"><tr><th className="px-4 py-3">Project</th><th>Company</th><th>Type</th><th>Amount</th><th>Status</th><th>Sent</th><th /></tr></thead>
        <tbody>
          {list.length === 0 && <tr><td colSpan={7} className="px-4 py-3 text-ink-500">No requests yet.</td></tr>}
          {list.map((r) => (
            <tr key={r.id} className="border-b border-ink-100">
              <td className="px-4 py-2">{r.project_title}</td><td>{r.company_name}<Demo on={r.company_is_demo} /></td>
              <td>{r.kind === "rff" ? "Request for funds" : `Proposal: ${r.call_title}`}</td><td>{inr(r.amount_inr)}</td>
              <td><b>{r.status}</b>{r.decision_note && <span className="block text-xs text-ink-500">“{r.decision_note}”</span>}</td>
              <td>{fmtDate(r.created_at)}</td>
              <td className="pr-3 text-right">{r.status === "pending" && <button className="btn-ghost min-h-[30px] px-3 py-1 text-xs" onClick={() => api.withdrawRequest(r.id).then(load)}>Withdraw</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Active({ role }) {
  const [list, setList] = useState(null);
  useEffect(() => { api.engagements().then(setList).catch(() => setList([])); }, []);
  if (!list) return <p className="text-sm text-ink-500">Loading…</p>;
  if (!list.length) return <p className="card text-sm text-ink-500">No accepted projects yet.</p>;
  return (
    <div className="space-y-4">
      {list.map((e) => <Engagement key={e.id} e={e} role={role} onChange={(n) => setList(list.map((x) => (x.id === n.id ? n : x)))} />)}
    </div>
  );
}
