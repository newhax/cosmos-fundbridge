import { useEffect, useState } from "react";
import { STATES, api, fmtDate } from "../../api.js";
import { Chips, Err, Field, Ok, Toggle } from "../../components/ui.jsx";

const RANGES = [["under_1000", "Under ₹1,000"], ["1000_5000", "₹1,000 to ₹5,000"], ["5000_25000", "₹5,000 to ₹25,000"],
  ["25000_100000", "₹25,000 to ₹1 lakh"], ["over_100000", "Over ₹1 lakh"]];
const FREQ = [["one_time", "One-time"], ["monthly", "Monthly"], ["yearly", "Yearly"]];

export default function DonorProfile({ status }) {
  const [f, setF] = useState(null);
  const [mem, setMem] = useState([]);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => { api.donorProfile().then(setF).catch((e) => setErr(e.message)); api.donorMemory().then(setMem).catch(() => {}); };
  useEffect(load, []);
  if (!f) return <p className="text-slate-500">Loading…</p>;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setErr(""); setMsg("");
    try {
      const { display_name, state, city, causes, giving_range, frequency, anonymous_by_default } = f;
      setF(await api.saveDonorProfile({ display_name, state, city, causes, giving_range, frequency, anonymous_by_default }));
      setMsg("Saved. The Matcher will recall these preferences."); load();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="card space-y-3 text-sm">
        <h2 className="text-lg font-semibold">Preferences</h2>
        <p className="text-xs text-slate-500">We keep only what the Matcher needs. India and INR only.</p>
        <Field label="Display name"><input className="input" value={f.display_name} onChange={set("display_name")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="State"><select className="input" value={f.state || ""} onChange={set("state")}><option value="">—</option>{STATES.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="City / district"><input className="input" value={f.city || ""} onChange={set("city")} /></Field>
        </div>
        <Field label="Causes you care about"><Chips options={status?.sectors || []} value={f.causes} onChange={(v) => setF({ ...f, causes: v })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Typical gift"><select className="input" value={f.giving_range || ""} onChange={set("giving_range")}><option value="">—</option>{RANGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Frequency"><select className="input" value={f.frequency || ""} onChange={set("frequency")}><option value="">—</option>{FREQ.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        </div>
        <Toggle checked={f.anonymous_by_default} onChange={(v) => setF({ ...f, anonymous_by_default: v })}
          label="Stay anonymous to NGOs by default" hint="You can still choose per gift. NGOs only ever see aggregate counts otherwise." />
        <Err msg={err} /><Ok msg={msg} />
        <button className="btn-primary" onClick={save}>Save preferences</button>
      </div>
      <div className="card text-sm">
        <h2 className="mb-2 text-lg font-semibold">What FundBridge remembers</h2>
        <p className="mb-2 text-xs text-slate-500">Your private memory. Only you and the Matcher use it; NGOs and companies never see it.</p>
        {mem.slice().reverse().map((m, i) => (
          <div key={i} className="border-b py-1 text-xs"><span className="text-slate-400">{fmtDate(m.at)} · {m.type}</span><div>{m.summary}</div></div>
        ))}
        {mem.length === 0 && <p className="text-xs text-slate-500">Nothing yet.</p>}
      </div>
    </div>
  );
}
