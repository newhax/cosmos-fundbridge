import { useEffect, useState } from "react";
import { api, fmtDate } from "../api.js";
import ModelTag, { ReviewBox } from "../components/ModelTag.jsx";

const ACTION_LABEL = {
  wait: "Wait",
  nudge: "Send a gentle nudge",
  new_angle_or_other_company: "Try a new angle or another company",
  reply_to_response: "Reply to their response",
  promise_reminder: "Remind them of their promise",
  send_first_email: "Send the first email",
  close_loop: "Close the loop",
};
const TYPE_STYLE = {
  drafted: "bg-slate-200 text-slate-700",
  sent: "bg-sky-100 text-sky-800",
  reply_received: "bg-emerald-100 text-emerald-800",
  promise_made: "bg-violet-100 text-violet-800",
  no_reply: "bg-rose-100 text-rose-800",
  viewed: "bg-slate-100 text-slate-600",
  interested: "bg-emerald-200 text-emerald-900",
  info_requested: "bg-amber-100 text-amber-800",
  declined: "bg-rose-100 text-rose-800",
  nudge_suggested: "bg-violet-50 text-violet-700",
  note: "bg-slate-100 text-slate-600",
};

export default function FollowUps() {
  const [data, setData] = useState(null);
  const load = () => api.followups().then(setData).catch(() => setData({ companies: [] }));
  useEffect(() => { load(); }, []);

  if (!data) return <p className="text-slate-500">Recalling history from memory…</p>;

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">
        History is recalled from the <b>{data.memory_backend === "meko" ? "Meko" : "local"}</b> memory store on every load, so it
        survives server restarts. In-app pitch threads come straight from the platform's event log (no pasting needed).
        Rules: nudge after 7 days of silence, new angle after 14, reminders on promised dates.
      </p>
      {data.companies.length === 0 && <p className="text-slate-500">No outreach yet. Draft and mark an email as sent first.</p>}
      {data.companies.map((c) => <CompanyCard key={c.key} c={c} reload={load} />)}
    </div>
  );
}

function CompanyCard({ c, reload }) {
  const [reply, setReply] = useState("");
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const s = c.next_step;

  const addReply = async () => {
    if (!reply.trim()) return;
    setBusy(true);
    await api.addEvent(c.company_id, { type: "reply_received", text: reply });
    setReply(""); setDraft(null); await reload(); setBusy(false);
  };
  const addNoReply = async () => { await api.addEvent(c.company_id, { type: "no_reply", text: "Marked: no reply yet." }); reload(); };
  const draftIt = async () => { setBusy(true); try { setDraft(await api.draftNext(c.key)); } finally { setBusy(false); } };
  const inApp = c.channel === "in_app";

  return (
    <div className="card">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="font-semibold">{c.company_name}</h3>
        <span className={`badge ${inApp ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{inApp ? `in-app pitch · ${c.status}` : "email"}</span>
        {s.overdue && <span className="badge bg-rose-600 text-white">OVERDUE</span>}
        <span className="ml-auto text-xs text-slate-500">{c.events.length} events recalled</span>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <ol className="relative border-l border-slate-200 pl-4">
          {c.events.map((e) => (
            <li key={e.event_id} className="mb-2">
              <div className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border-2 border-white bg-slate-400" />
              <div className="text-xs text-slate-500">
                {fmtDate(e.at)} <span className={`badge ${TYPE_STYLE[e.type]}`}>{e.type}</span>
                {e.simulated && <span className="badge ml-1 bg-amber-100 text-amber-800">simulated</span>}
                <span className="ml-1 font-mono text-[10px] text-slate-400">{e.event_id}</span>
              </div>
              {e.type === "promise_made" ? (
                <p className="text-sm">“{e.promise_what}” · by <b>{e.promise_by || "unknown"}</b></p>
              ) : (
                e.text && <p className="line-clamp-3 whitespace-pre-line text-sm text-slate-700">{e.text}</p>
              )}
            </li>
          ))}
        </ol>
        <div className="space-y-2">
          <div className={`rounded-box border p-2 ${s.overdue ? "border-rose-300 bg-rose-50" : "border-emerald-200 bg-emerald-50"}`}>
            <div className="text-xs uppercase text-slate-500">Suggested next step {s.due && <>· due {s.due}</>}</div>
            <div className="font-semibold">{ACTION_LABEL[s.action]}</div>
            <p className="text-xs text-slate-600">{s.reason}</p>
            {s.action !== "wait" && s.action !== "send_first_email" && (
              <button className="btn-primary mt-2" onClick={draftIt} disabled={busy}>{busy ? "Drafting…" : "Draft it"}</button>
            )}
          </div>
          {draft && (
            <div className="rounded-box border border-slate-200 p-2">
              <div className="mb-1 flex flex-wrap items-center gap-1 text-xs text-slate-500">
                Draft based on {draft.recalled_events.length} recalled events ·
                <ModelTag status={draft.next_step.status} producedBy={draft.next_step.produced_by} fallbackFrom={draft.next_step.fallback_from} />
              </div>
              {draft.next_step.why && <p className="mb-1 text-xs text-slate-700"><b>Model's reasoning:</b> {draft.next_step.why}</p>}
              <ReviewBox problems={draft.next_step.review_problems} rejected={draft.next_step.rejected} />
              <textarea key={draft.next_step.draft} className="input h-40 text-sm" defaultValue={draft.next_step.draft} />
              <button className="btn-ghost mt-1 text-xs" onClick={() => navigator.clipboard.writeText(draft.next_step.draft)}>Copy</button>
            </div>
          )}
          {!inApp && <div>
            <label className="label">Paste their reply</label>
            <textarea className="input h-20 text-sm" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="e.g. Thank you, our committee will review this by 20 November." />
            <div className="mt-1 flex gap-2">
              <button className="btn-ghost" onClick={addReply} disabled={busy}>Save reply (extracts promises)</button>
              <button className="btn-ghost" onClick={addNoReply}>Log: no reply</button>
            </div>
          </div>}
        </div>
      </div>
    </div>
  );
}
