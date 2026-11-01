import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useStates } from "../../components/geo.jsx";
import NgoListingCard from "../../components/NgoListingCard.jsx";

// NGO directory, ranked by fit with this company's own profile (focus sectors and states).
// The score is computed on the server from listed fields only; nothing the NGO did not list is guessed.
export default function CompanyDirectory({ status, onEditProfile }) {
  const lgdStates = useStates();
  const [f, setF] = useState({ sector: "", state: "", district: "" });
  const [list, setList] = useState(null);
  const [profile, setProfile] = useState(null);
  useEffect(() => { api.companyProfile().then(setProfile).catch(() => {}); }, []);
  useEffect(() => { api.directory(f).then(setList).catch(() => setList([])); }, [f]);
  const sectors = profile?.focus_sectors || [];
  const states = profile?.focus_states || [];

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
        <div>
          <p className="font-semibold">Recommended for you</p>
          <p className="text-xs text-ink-500">
            Ranked by your profile: focus sectors <b>{sectors.join(", ") || "not set"}</b> · focus states{" "}
            <b>{states.join(", ") || "not set"}</b>. Cause fit 50%, state 35%, listed registrations 15%.
          </p>
        </div>
        {onEditProfile && <button className="btn-ghost min-h-[36px] text-xs" onClick={onEditProfile}>Edit focus</button>}
      </div>
      {profile && !sectors.length && !states.length && (
        <p className="rounded-box bg-sand-50 px-4 py-2.5 text-xs text-sand-900">
          Set focus sectors and states in Profile &amp; footprint to get recommendations; until then every NGO scores the same.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <select aria-label="Sector" className="input w-48" value={f.sector} onChange={(e) => setF({ ...f, sector: e.target.value })}>
          <option value="">All sectors</option>{(status?.sectors || []).map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="State" className="input w-48" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
          <option value="">All states</option>{lgdStates.map((s) => <option key={s.lgd_code} value={s.name}>{s.name}</option>)}</select>
        <input aria-label="District" className="input w-48" placeholder="District" value={f.district} onChange={(e) => setF({ ...f, district: e.target.value })} />
      </div>
      <p className="text-xs text-ink-500">Only NGOs that opted in are listed, showing only the fields they chose. NGOs you reported are hidden.</p>

      <div className="grid gap-3 md:grid-cols-2">
        {(list || []).map((n, i) => (
          <div key={n.ngo_id} className="card space-y-3">
            <FitBar rank={i + 1} fit={n.fit} />
            <NgoListingCard n={n} className="" />
          </div>
        ))}
        {list && list.length === 0 && <p className="text-sm text-ink-500">No listed NGOs match.</p>}
      </div>
    </div>
  );
}

function FitBar({ rank, fit }) {
  if (!fit) return null;
  const tone = fit.total >= 70 ? "bg-sage-500 text-white" : fit.total >= 40 ? "bg-ink-900 text-white" : "bg-ink-200 text-ink-700";
  return (
    <div className="space-y-1.5 border-b border-ink-100 pb-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-ink-400">#{rank}</span>
        <span className={`badge ${tone}`} title="Fit with your profile (0-100)">{fit.total} fit</span>
      </div>
      {fit.reasons.length > 0 && (
        <ul className="space-y-0.5 text-xs text-ink-700">{fit.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
      )}
      {fit.unknowns.length > 0 && <p className="text-[11px] text-ink-400">Unknown: {fit.unknowns.join("; ")}</p>}
    </div>
  );
}
