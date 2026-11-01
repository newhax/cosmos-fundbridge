import { useEffect, useState } from "react";
import { api, inr } from "../../api.js";
import { Demo } from "../../components/ui.jsx";

const COLS = [["sent", "New"], ["viewed", "Viewed"], ["info_requested", "Need more info"], ["interested", "Interested"], ["declined", "Not a fit"]];

export default function CompanyPipeline() {
  const [data, setData] = useState(null);
  useEffect(() => { api.pipeline().then(setData).catch(() => {}); }, []);
  if (!data) return <p className="text-slate-500">Loading…</p>;
  return (
    <div>
      <p className="mb-3 text-sm text-slate-600">{data.count} pitches · promised so far: <b>{inr(data.promised_total_inr)}</b></p>
      <div className="grid gap-3 md:grid-cols-5">
        {COLS.map(([k, label]) => (
          <div key={k} className="rounded-card bg-slate-100 p-2">
            <div className="mb-2 text-xs font-bold uppercase text-slate-500">{label} ({(data.columns[k] || []).length})</div>
            {(data.columns[k] || []).map((p) => (
              <div key={p.id} className="card mb-2 p-2 text-xs">
                <div className="font-semibold">{p.ngo_name}<Demo on={p.ngo_is_demo} /></div>
                <div className="truncate">{p.subject}</div>
                <div className="text-slate-500">ask {inr(p.ask_inr)}</div>
                {p.promise && <div className="mt-1 rounded-box bg-violet-50 p-1 text-violet-800">Promise: {p.promise.what} · {inr(p.promise.amount_inr)} · due {p.promise.due_date || "unknown"}</div>}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
