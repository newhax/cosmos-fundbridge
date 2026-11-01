// One accepted request ("engagement") managed by both sides. The company adds milestones and closes it;
// the NGO marks milestones done and posts progress and fund-utilisation updates. Nothing moves money.
import { useState } from "react";
import { api, fmtDate, inr } from "../api.js";
import { Demo, Err } from "./ui.jsx";

const KINDS = { company: [["milestone", "Milestone"], ["note", "Note"]],
  ngo: [["progress", "Progress"], ["utilisation", "Fund utilisation"], ["note", "Note"]] };

export default function Engagement({ e, role, onChange }) {
  const [f, setF] = useState({ kind: KINDS[role][0][0], title: "", body: "", percent: "", amount_inr: "", due_date: "" });
  const [err, setErr] = useState("");
  const s = e.summary;
  const add = async (ev) => {
    ev.preventDefault(); setErr("");
    try { onChange(await api.addUpdate(e.id, f)); setF({ ...f, title: "", body: "", percent: "", amount_inr: "", due_date: "" }); }
    catch (x) { setErr(x.message); }
  };
  const act = (fn) => async () => { setErr(""); try { onChange(await fn()); } catch (x) { setErr(x.message); } };
  const other = role === "ngo" ? <>{e.company_name}<Demo on={e.company_is_demo} /></> : <>{e.ngo_name}<Demo on={e.ngo_is_demo} /></>;
  const pct = s.latest_progress ?? 0;
  return (
    <article className="card space-y-4 text-sm">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">{e.project_title}</h3>
          <p className="text-xs text-ink-500">With {other} · {e.kind === "proposal" ? `proposal to “${e.call_title}”` : "request for funds"} · accepted {fmtDate(e.decided_at)}</p>
        </div>
        <span className={`badge ${e.status === "completed" ? "bg-sage-100 text-sage-900" : "bg-ink-900 text-white"}`}>{e.status}</span>
      </header>

      <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="rounded-box bg-ink-50 p-2"><dt className="text-ink-500">Agreed (stated)</dt><dd className="font-semibold">{inr(s.agreed_inr)}</dd></div>
        <div className="rounded-box bg-ink-50 p-2"><dt className="text-ink-500">Utilisation reported</dt><dd className="font-semibold">{inr(s.utilised_inr)}</dd></div>
        <div className="rounded-box bg-ink-50 p-2"><dt className="text-ink-500">Milestones done</dt><dd className="font-semibold">{s.milestones_done} / {s.milestones}</dd></div>
        <div className="rounded-box bg-ink-50 p-2"><dt className="text-ink-500">Progress (NGO)</dt><dd className="font-semibold">{s.latest_progress == null ? "unknown" : `${pct}%`}</dd></div>
      </dl>
      <div className="h-2 rounded-full bg-ink-100" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Reported progress">
        <div className="h-2 rounded-full bg-sage-500" style={{ width: `${pct}%` }} />
      </div>
      {s.overdue.length > 0 && <p className="rounded-box bg-coral-50 px-3 py-2 text-xs text-coral-800">Overdue milestones: {s.overdue.join(", ")}</p>}

      <ol className="space-y-2 border-l border-ink-200 pl-4">
        {e.updates.length === 0 && <li className="text-xs text-ink-500">No updates yet.</li>}
        {e.updates.map((u) => (
          <li key={u.id} className="text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="badge bg-ink-100 text-ink-700">{u.kind}</span>
              <b>{u.title}</b>
              {u.kind === "milestone" && (role === "ngo" && e.status !== "completed"
                ? <label className="flex items-center gap-1"><input type="checkbox" checked={u.done} onChange={act(() => api.milestoneDone(e.id, u.id, !u.done))} /> done</label>
                : <span>{u.done ? "✓ done" : "open"}</span>)}
              {u.due_date && <span className="text-ink-500">due {fmtDate(u.due_date)}</span>}
              {u.percent != null && <span>{u.percent}%</span>}
              {u.amount_inr != null && <span>{inr(u.amount_inr)}</span>}
              <span className="text-ink-400">{u.author_role} · {fmtDate(u.created_at)}</span>
            </div>
            {u.body && <p className="mt-0.5 whitespace-pre-line text-ink-600">{u.body}</p>}
          </li>
        ))}
      </ol>

      {e.status !== "completed" && (
        <form onSubmit={add} className="grid gap-2 rounded-box bg-ink-50 p-3 sm:grid-cols-2">
          <div><label className="label" htmlFor={`k-${e.id}`}>Add</label>
            <select id={`k-${e.id}`} className="input" value={f.kind} onChange={(x) => setF({ ...f, kind: x.target.value })}>
              {KINDS[role].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <div><label className="label" htmlFor={`t-${e.id}`}>Title</label>
            <input id={`t-${e.id}`} className="input" required minLength={3} value={f.title} onChange={(x) => setF({ ...f, title: x.target.value })} /></div>
          {f.kind === "progress" && <div><label className="label" htmlFor={`p-${e.id}`}>Progress %</label>
            <input id={`p-${e.id}`} className="input" type="number" min={0} max={100} required value={f.percent} onChange={(x) => setF({ ...f, percent: x.target.value })} /></div>}
          {f.kind === "utilisation" && <div><label className="label" htmlFor={`a-${e.id}`}>Amount spent (INR)</label>
            <input id={`a-${e.id}`} className="input" type="number" min={1} required value={f.amount_inr} onChange={(x) => setF({ ...f, amount_inr: x.target.value })} /></div>}
          {f.kind === "milestone" && <div><label className="label" htmlFor={`d-${e.id}`}>Due date</label>
            <input id={`d-${e.id}`} className="input" type="date" value={f.due_date} onChange={(x) => setF({ ...f, due_date: x.target.value })} /></div>}
          <div className="sm:col-span-2"><label className="label" htmlFor={`b-${e.id}`}>Details (optional)</label>
            <textarea id={`b-${e.id}`} className="input" rows={2} value={f.body} onChange={(x) => setF({ ...f, body: x.target.value })} /></div>
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <button className="btn-primary min-h-[38px] text-xs">Add update</button>
            {role === "company" && <button type="button" className="btn-ghost min-h-[38px] text-xs"
              onClick={() => window.confirm("Mark this project as completed? Updates stop after this.") && act(() => api.completeEngagement(e.id))()}>Mark completed</button>}
          </div>
        </form>
      )}
      <Err msg={err} />
    </article>
  );
}
