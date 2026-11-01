import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../../api.js";
import { Demo } from "../../components/ui.jsx";

export default function DonorLists() {
  const [lists, setLists] = useState(null);
  const [gifts, setGifts] = useState([]);
  const [feed, setFeed] = useState([]);
  const load = () => {
    api.donorLists().then(setLists).catch(() => {});
    api.myGifts().then(setGifts).catch(() => {});
    api.feed().then(setFeed).catch(() => {});
  };
  useEffect(load, []);

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card text-sm">
        <h3 className="mb-2 font-semibold">Shortlist</h3>
        {lists?.shortlist.map((n) => (
          <div key={n.ngo_id} className="flex justify-between border-b py-1">
            <span>{n.name}</span><button className="text-xs text-slate-500 underline" onClick={() => api.shortlist(n.ngo_id, false).then(load)}>remove</button>
          </div>
        ))}
        {lists?.shortlist.length === 0 && <p className="text-xs text-slate-500">Nothing shortlisted yet.</p>}
        <h3 className="mb-2 mt-4 font-semibold">Following</h3>
        {lists?.following.map((n) => (
          <div key={n.ngo_id} className="flex justify-between border-b py-1">
            <span>{n.name}</span><button className="text-xs text-slate-500 underline" onClick={() => api.follow(n.ngo_id, false).then(load)}>unfollow</button>
          </div>
        ))}
      </div>
      <div className="card text-sm">
        <h3 className="mb-2 font-semibold">My gifts (self-reported)</h3>
        {gifts.map((g) => (
          <div key={g.id} className="border-b py-1">
            <div className="flex justify-between"><span>{g.ngo_name}</span><b>{inr(g.amount_inr)}</b></div>
            <div className="text-[11px] text-slate-500">{g.date} · <span className={`badge ${g.status === "self-reported" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>{g.status}</span> · {g.share_name ? "name shown to NGO" : "anonymous to NGO"}</div>
          </div>
        ))}
        {gifts.length === 0 && <p className="text-xs text-slate-500">No gifts reported yet.</p>}
        <p className="mt-2 text-[11px] text-slate-400">FundBridge stores no payment data. Ask the NGO for an 80G receipt. This is not tax advice.</p>
      </div>
      <div className="card text-sm">
        <h3 className="mb-2 font-semibold">Updates from NGOs you follow</h3>
        {feed.map((u) => (
          <div key={u.id} className="border-b py-1.5">
            <div className="text-[11px] text-slate-500">{fmtDate(u.created_at)} · {u.ngo_name}<Demo on={u.is_demo} /> · <i>{u.label}</i></div>
            <b>{u.title}</b><p className="text-xs">{u.body}</p>
          </div>
        ))}
        {feed.length === 0 && <p className="text-xs text-slate-500">Follow NGOs to see their updates here.</p>}
      </div>
    </div>
  );
}
