// Regional admin (scoped by state and language): location and sector review queues, the sector map with
// Schedule VII references, state CSR portal links, and feedback. No company approval or manual verification.
import { useEffect, useState } from "react";
import { api, fmtDate } from "../api.js";
import { DistrictSelect, StateSelect, useStates } from "../components/geo.jsx";
import { Err, Ok } from "../components/ui.jsx";

export default function Admin({ tab, status }) {
  const [scope, setScope] = useState(null);
  useEffect(() => { api.adminScope().then(setScope).catch(() => {}); }, []);
  return (
    <div className="space-y-4">
      {scope && <p className="text-xs text-ink-500">Your scope: {scope.state_names?.length ? scope.state_names.join(", ") : "all states"} ·
        {" "}{scope.languages?.length ? scope.languages.join(", ") : "all languages"}</p>}
      {tab === "queues" && <Locations scope={scope} />}
      {tab === "sectors" && <Sectors status={status} scope={scope} />}
      {tab === "portals" && <Portals scope={scope} />}
      {tab === "feedback" && <Feedback />}
    </div>
  );
}

function Locations() {
  const [f, setF] = useState({ status: "open", state: "" });
  const [list, setList] = useState([]);
  const [pick, setPick] = useState({});
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.adminUnmapped(f.status, f.state).then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [f]);
  const resolve = async (it, target) => {
    setErr(""); setMsg("");
    try { await api.adminResolve(it.id, target); setMsg(target ? `Mapped “${it.raw}”. It is now an alias.` : `Ignored “${it.raw}”.`); load(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <section className="card space-y-3 text-sm">
      <h2 className="text-lg font-semibold">Unmapped locations</h2>
      <p className="text-xs text-ink-500">Names from profiles and filings that did not match the LGD list exactly. Nothing is matched automatically: map each to the right LGD entry, or ignore it.</p>
      <div className="flex flex-wrap gap-2">
        <select aria-label="Status" className="input w-40" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="open">open</option><option value="resolved">resolved</option><option value="ignored">ignored</option></select>
        <StateSelect className="input w-56" allowAll value={f.state} onChange={(v) => setF({ ...f, state: v })} />
      </div>
      <Err msg={err} /><Ok msg={msg} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="border-b border-ink-200"><tr><th className="py-2">Name as written</th><th>Kind</th><th>State</th><th>Seen</th><th>Where</th><th>Map to</th></tr></thead>
          <tbody>
            {list.length === 0 && <tr><td colSpan={6} className="py-3 text-ink-500">Nothing in this queue.</td></tr>}
            {list.map((it) => (
              <tr key={it.id} className="border-b border-ink-100 align-top">
                <td className="py-2 font-medium">{it.raw}</td><td>{it.kind}</td><td>{it.state_name}</td><td>{it.count}×</td>
                <td className="max-w-[200px]">{it.context}</td>
                <td className="space-y-1 py-2">
                  {it.status === "open" ? (<>
                    {it.kind === "state"
                      ? <StateSelect className="input py-1 text-xs" value={pick[it.id] || ""} onChange={(v) => setPick({ ...pick, [it.id]: v })} />
                      : <DistrictSelect className="input py-1 text-xs" state={it.state_lgd_code} wholeLabel="Choose district" value={pick[it.id] || ""} onChange={(v) => setPick({ ...pick, [it.id]: v })} />}
                    <div className="flex gap-1">
                      <button className="btn-primary min-h-[28px] px-3 py-0.5 text-[11px]" disabled={!pick[it.id]} onClick={() => resolve(it, pick[it.id])}>Map</button>
                      <button className="btn-ghost min-h-[28px] px-3 py-0.5 text-[11px]" onClick={() => resolve(it, null)}>Ignore</button>
                    </div>
                  </>) : <span>{it.status} {it.resolved_code ? `→ ${it.resolved_code}` : ""} by {it.resolved_by}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Sectors({ status, scope }) {
  const [d, setD] = useState(null);
  const [map, setMap] = useState({});
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.adminSectors().then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const national = scope && !scope.states;
  if (!d) return <p className="text-sm text-ink-500">Loading…</p>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="card space-y-2 text-sm">
        <h2 className="text-lg font-semibold">Sectors and Schedule VII</h2>
        <p className="text-xs text-ink-500">{d.reference._about}</p>
        <p className="text-xs">Source: <a className="underline" href={d.reference.official_source} target="_blank" rel="noopener noreferrer">India Code, Schedule VII</a>. {d.reference.retrieved}</p>
        <p className="text-xs">Local-area spend: {d.local_area_spend}.</p>
        <ul className="space-y-2">
          {d.sectors.map((s) => (
            <li key={s.sector} className="rounded-box border border-ink-200 p-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2"><b>{s.sector}</b>
                <span className={`badge ${s.status === "verified" ? "bg-sage-100 text-sage-900" : "bg-sand-100 text-sand-900"}`}>{s.status}</span></div>
              {s.schedule_vii.map((r) => <p key={r.item}>Item {r.item}: {r.text || "text not loaded"}{r.verified ? "" : " (not yet checked)"}</p>)}
              {national && <SectorItems sector={s} onSaved={load} />}
            </li>
          ))}
        </ul>
      </section>
      <section className="card space-y-2 text-sm">
        <h2 className="text-lg font-semibold">Unmapped sector labels</h2>
        <p className="text-xs text-ink-500">Labels in the filings that fell into “other”. A mapping applies from the next dataset version (python scripts/ingest.py).</p>
        <Err msg={err} /><Ok msg={msg} />
        <ul className="space-y-1.5">
          {d.unmapped_labels.length === 0 && <li className="text-xs text-ink-500">None.</li>}
          {d.unmapped_labels.map((u) => (
            <li key={u.sector_raw} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="min-w-[160px] flex-1">{u.sector_raw || "(blank)"} <span className="text-ink-400">· {u.n_rows} rows, {u.n_companies} companies</span></span>
              <select aria-label={`Sector for ${u.sector_raw}`} className="input w-40 py-1 text-xs" value={map[u.sector_raw] || ""} onChange={(e) => setMap({ ...map, [u.sector_raw]: e.target.value })}>
                <option value="">Map to…</option>{(status?.sectors || []).map((s) => <option key={s}>{s}</option>)}</select>
              <button className="btn-ghost min-h-[28px] px-3 py-0.5 text-[11px]" disabled={!map[u.sector_raw]}
                onClick={() => api.adminMapLabel(u.sector_raw, map[u.sector_raw]).then((r) => { setMsg(`Saved; ${r.applies}.`); load(); }).catch((e) => setErr(e.message))}>Save</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function SectorItems({ sector, onSaved }) {
  const [items, setItems] = useState(sector.schedule_vii.map((r) => r.item).join(", "));
  const [note, setNote] = useState("");
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      <input aria-label={`Schedule VII items for ${sector.sector}`} className="input w-28 py-1 text-xs" value={items} onChange={(e) => setItems(e.target.value)} placeholder="(i), (ii)" />
      <input aria-label="Where you checked" className="input min-w-[140px] flex-1 py-1 text-xs" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Where you checked the text" />
      <button className="btn-ghost min-h-[28px] px-3 py-0.5 text-[11px]" onClick={() => api.adminSectorItems(sector.sector, items.split(",").map((x) => x.trim()).filter(Boolean), note).then(onSaved)}>Save</button>
    </div>
  );
}

function Portals({ scope }) {
  const states = useStates();
  const [portals, setPortals] = useState({});
  const [edit, setEdit] = useState({});
  const [err, setErr] = useState("");
  const load = () => api.statePortals().then(setPortals).catch(() => {});
  useEffect(() => { load(); }, []);
  const mine = scope?.states ? states.filter((s) => scope.states.includes(s.lgd_code)) : states;
  const save = async (s) => {
    setErr("");
    try { await api.adminSetPortal(s.lgd_code, edit[s.lgd_code]?.url || null, edit[s.lgd_code]?.note || ""); setEdit({ ...edit, [s.lgd_code]: undefined }); load(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <section className="card space-y-3 text-sm">
      <h2 className="text-lg font-semibold">State CSR portals</h2>
      <p className="text-xs text-ink-500">Links are shown to users only when set here. Add one only after opening it, and say where you confirmed it. Never guess a URL.</p>
      <Err msg={err} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="border-b border-ink-200"><tr><th className="py-2">State / UT</th><th>Link</th><th>Confirmed where</th><th /></tr></thead>
          <tbody>{mine.map((s) => {
            const cur = portals[s.lgd_code]; const e = edit[s.lgd_code];
            return (
              <tr key={s.lgd_code} className="border-b border-ink-100">
                <td className="py-2 font-medium">{s.name}</td>
                <td>{e ? <input aria-label={`Portal URL for ${s.name}`} className="input py-1 text-xs" value={e.url} onChange={(x) => setEdit({ ...edit, [s.lgd_code]: { ...e, url: x.target.value } })} placeholder="https://" />
                  : cur ? <a className="underline" href={cur.url} target="_blank" rel="noopener noreferrer">{cur.url}</a> : <span className="text-ink-400">not set</span>}</td>
                <td>{e ? <input aria-label="Where confirmed" className="input py-1 text-xs" value={e.note} onChange={(x) => setEdit({ ...edit, [s.lgd_code]: { ...e, note: x.target.value } })} /> : cur?.note}</td>
                <td className="text-right">{e
                  ? <button className="btn-primary min-h-[28px] px-3 py-0.5 text-[11px]" onClick={() => save(s)}>Save</button>
                  : <button className="btn-ghost min-h-[28px] px-3 py-0.5 text-[11px]" onClick={() => setEdit({ ...edit, [s.lgd_code]: { url: cur?.url || "", note: cur?.note || "" } })}>Edit</button>}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
    </section>
  );
}

function Feedback() {
  const [f, setF] = useState({ status: "", state: "", language: "" });
  const [list, setList] = useState([]);
  const [err, setErr] = useState("");
  const load = () => api.adminFeedback(f).then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [f]);
  return (
    <section className="card space-y-3 text-sm">
      <h2 className="text-lg font-semibold">Feedback</h2>
      <div className="flex flex-wrap gap-2">
        <select aria-label="Status" className="input w-36" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="">Any status</option><option value="new">new</option><option value="read">read</option><option value="done">done</option></select>
        <StateSelect className="input w-56" allowAll value={f.state} onChange={(v) => setF({ ...f, state: v })} />
        <input aria-label="Language code" className="input w-28" placeholder="language" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })} />
      </div>
      <Err msg={err} />
      <ul className="space-y-2">
        {list.length === 0 && <li className="text-xs text-ink-500">No feedback.</li>}
        {list.map((x) => (
          <li key={x.id} className="rounded-box border border-ink-200 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2"><span className="badge bg-ink-100 text-ink-700">{x.category}</span>
              {x.rating && <span>{"★".repeat(x.rating)}</span>}<span className="text-ink-400">{fmtDate(x.created_at)} · {x.role || "visitor"} · {x.language} · {x.page}</span>
              <select aria-label="Status" className="input ml-auto w-28 py-1 text-xs" value={x.status} onChange={(e) => api.adminFeedbackStatus(x.id, e.target.value).then(load)}>
                <option>new</option><option>read</option><option>done</option></select></div>
            <p className="mt-1 whitespace-pre-line">{x.message}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
