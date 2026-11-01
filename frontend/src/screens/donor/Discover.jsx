import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../../api.js";
import { useStates } from "../../components/geo.jsx";
import { CredBadge, Demo, Err } from "../../components/ui.jsx";
import VerificationBadge from "../../components/VerificationBadge.jsx";

export default function DonorDiscover({ status }) {
  const lgdStates = useStates();
  const [f, setF] = useState({ cause: "", state: "" });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [leaving, setLeaving] = useState(null);
  const [gift, setGift] = useState(null);

  const load = () => api.discover(f).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [f]);

  const toggle = async (fn, ngoId, on) => { await fn(ngoId, on); load(); };
  const give = async (ngoId) => setLeaving(await api.give(ngoId));
  const report = async (ngoId) => {
    const reason = window.prompt("What is wrong with this NGO profile?");
    if (reason) { await api.reportNgo(ngoId, reason); load(); }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="Cause" className="input w-48" value={f.cause} onChange={(e) => setF({ ...f, cause: e.target.value })}>
          <option value="">All causes</option>{(status?.sectors || []).map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="State" className="input w-48" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
          <option value="">All states</option>{lgdStates.map((s) => <option key={s.lgd_code} value={s.name}>{s.name}</option>)}</select>
        {data && <span className="text-xs text-slate-500">Matcher recalled {data.memory_recalled} memory items first · {data.notice}</span>}
      </div>
      <Err msg={err} />
      {data?.recommendations.length === 0 && <p className="text-sm text-slate-500">No NGOs have opted in to individual donations for these filters.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {data?.recommendations.map((r) => (
          <div key={r.ngo.ngo_id} className="card space-y-2 text-sm">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-semibold">{r.ngo.name}<Demo on={r.ngo.is_demo} /></div>
                <div className="text-xs text-slate-500">{[r.ngo.district, r.ngo.state].filter(Boolean).join(", ")} · {(r.ngo.causes || []).join(", ")}</div>
              </div>
              <div className="text-right"><div className="text-xl font-bold">{r.score.total}</div><div className="text-[10px] text-slate-400">match</div></div>
            </div>
            <VerificationBadge v={r.ngo.verification} />
            <p>{r.why} {r.why_mode === "llm" && <span className="badge bg-violet-100 text-violet-800">AI ({r.produced_by}), fact-checked</span>}</p>
            {r.memory_used.length > 0 && (
              <div className="rounded-box bg-sky-50 p-1.5 text-[11px] text-sky-900">
                Used from your memory: {r.memory_used.map((m) => `${m.text}`).join(" · ")}
              </div>
            )}
            <details className="text-[11px] text-slate-600">
              <summary className="cursor-pointer">Credentials and decision trace</summary>
              <table className="mt-1 w-full">
                <tbody>{r.ngo.credentials_panel.map((c) => (
                  <tr key={c.label}><td>{c.label}</td><td>{c.value || "—"}</td><td><CredBadge source={c.status} /></td></tr>))}</tbody>
              </table>
              <div className="mt-1">Score: {Object.entries(r.score.components).map(([k, v]) => `${k} ${Math.round(v * 100)}% × ${r.score.weights[k]}`).join(" · ")}</div>
              <div>{r.score.scored_on}. {r.score.unknowns.join("; ")}</div>
              {r.ngo.suggested_gift_inr && <div>Suggested gift: {inr(r.ngo.suggested_gift_inr)} (from the NGO)</div>}
            </details>
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary" onClick={() => give(r.ngo.ngo_id)}>Give</button>
              <button className="btn-ghost" onClick={() => toggle(api.shortlist, r.ngo.ngo_id, !r.shortlisted)}>{r.shortlisted ? "★ Shortlisted" : "☆ Shortlist"}</button>
              <button className="btn-ghost" onClick={() => toggle(api.follow, r.ngo.ngo_id, !r.following)}>{r.following ? "Following" : "Follow"}</button>
              <button className="btn-ghost" onClick={() => setGift({ ngo: r.ngo, amount: "", date: new Date().toISOString().slice(0, 10), share: null })}>I donated</button>
              <button className="btn-ghost ml-auto text-xs text-rose-700" onClick={() => report(r.ngo.ngo_id)}>Report</button>
            </div>
          </div>
        ))}
      </div>
      {leaving && <LeavingModal g={leaving} onClose={() => setLeaving(null)} />}
      {gift && <GiftModal g={gift} setG={setGift} onDone={() => { setGift(null); load(); }} />}
    </div>
  );
}

function LeavingModal({ g, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="max-w-md space-y-3 rounded-card bg-white p-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-semibold">Give to {g.ngo_name}</h3>
        <p className="rounded-box bg-amber-50 p-2 text-sm text-amber-900">{g.notice}</p>
        <p className="text-xs text-slate-500">{g.india_only}</p>
        <p className="break-all text-xs">Destination: <code>{g.url}</code></p>
        <div className="flex gap-2">
          <a className="btn-primary" href={g.url} target="_blank" rel="noopener noreferrer" onClick={onClose}>Continue to the NGO's page ↗</a>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function GiftModal({ g, setG, onDone }) {
  const [err, setErr] = useState("");
  const save = async () => {
    try {
      await api.reportGift({ ngo_id: g.ngo.ngo_id, amount_inr: Number(g.amount), date: g.date, share_name: g.share });
      onDone();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-w-sm space-y-2 rounded-card bg-white p-4">
        <h3 className="font-semibold">I donated to {g.ngo.name}</h3>
        <p className="text-xs text-slate-500">Self-reported. No payment data is stored. The NGO may mark it as confirmed.</p>
        <input aria-label="Amount (INR)" className="input" type="number" min="1" placeholder="Amount (INR)" value={g.amount} onChange={(e) => setG({ ...g, amount: e.target.value })} />
        <input aria-label="Date of gift" className="input" type="date" value={g.date} onChange={(e) => setG({ ...g, date: e.target.value })} />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={!!g.share} onChange={(e) => setG({ ...g, share: e.target.checked })} /> Show my name to this NGO
        </label>
        <Err msg={err} />
        <div className="flex gap-2"><button className="btn-primary" onClick={save} disabled={!g.amount}>Save</button><button className="btn-ghost" onClick={onDone}>Cancel</button></div>
      </div>
    </div>
  );
}
