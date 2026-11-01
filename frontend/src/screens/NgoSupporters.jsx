import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../api.js";
import ModelTag from "../components/ModelTag.jsx";
import { Err, Ok } from "../components/ui.jsx";

export default function NgoSupporters() {
  const [sup, setSup] = useState(null);
  const [updates, setUpdates] = useState([]);
  const [facts, setFacts] = useState("");
  const [draft, setDraft] = useState(null);
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    api.supporters().then(setSup).catch((e) => setErr(e.message));
    api.myUpdates().then(setUpdates).catch(() => {});
  };
  useEffect(load, []);

  const makeDraft = async () => { setErr(""); try { setDraft(await api.draftUpdate(facts)); setOk(false); } catch (e) { setErr(e.message); } };
  const post = async () => {
    setErr("");
    try { await api.postUpdate(draft.title, draft.body); setDraft(null); setFacts(""); setMsg("Update posted to your followers."); load(); }
    catch (e) { setErr(e.message); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="card text-sm">
        <h2 className="mb-2 text-lg font-semibold">Individual supporters</h2>
        {sup && (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Stat label="Followers" v={sup.followers} />
              <Stat label="Gifts reported" v={sup.gifts_reported} />
              <Stat label="Total reported" v={inr(sup.gifts_total_inr)} />
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Named followers (opted in): {sup.named_followers.join(", ") || "none"}. Everyone else is anonymous to you.
            </p>
            <table className="mt-3 w-full text-xs">
              <thead className="text-slate-400"><tr><th className="text-left">Donor</th><th>Date</th><th className="text-right">Amount</th><th>Status</th><th /></tr></thead>
              <tbody>
                {sup.gifts.map((g) => (
                  <tr key={g.id} className="border-t">
                    <td>{g.donor}</td><td className="text-center">{g.date}</td><td className="text-right">{inr(g.amount_inr)}</td>
                    <td className="text-center"><span className={`badge ${g.status === "self-reported" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>{g.status}</span></td>
                    <td>{g.status === "self-reported" && <button className="btn-ghost text-[11px]" onClick={() => api.confirmGift(g.id).then(load)}>Confirm received</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-slate-400">{sup.note}</p>
          </>
        )}
      </div>
      <div className="card space-y-2 text-sm">
        <h2 className="text-lg font-semibold">Post an update</h2>
        <p className="text-xs text-slate-500">Write your own notes. The Writer can tidy them up, but it may not add figures or claims you did not supply. You review before posting.</p>
        <textarea className="input h-24" value={facts} onChange={(e) => setFacts(e.target.value)} placeholder="e.g. Started 2 new reading circles in Barmer this month." />
        <button className="btn-ghost" disabled={facts.trim().length < 10} onClick={makeDraft}>Draft with Writer</button>
        {draft && (
          <div className="space-y-2 rounded-box border p-2">
            <ModelTag status={draft.status} producedBy={draft.produced_by} />
            <input className="input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            <textarea className="input h-28" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I checked every fact in this update</label>
            <button className="btn-primary" disabled={!ok} onClick={post}>Post update</button>
          </div>
        )}
        <Err msg={err} /><Ok msg={msg} />
        <h3 className="border-t pt-2 font-semibold">Your updates</h3>
        {updates.map((u) => (
          <div key={u.id} className="border-b pb-1">
            <div className="text-xs text-slate-400">{fmtDate(u.created_at)} · {u.label}</div>
            <b>{u.title}</b><p className="text-xs">{u.body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

const Stat = ({ label, v }) => (
  <div className="rounded-box bg-slate-50 p-2"><div className="text-lg font-bold">{v}</div><div className="text-[11px] text-slate-500">{label}</div></div>
);
