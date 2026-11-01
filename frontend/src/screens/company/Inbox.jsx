import { useEffect, useState } from "react";
import { api, fmtDate, inr } from "../../api.js";
import ModelTag, { ReviewBox } from "../../components/ModelTag.jsx";
import { SourceChip } from "../../components/Sources.jsx";
import { CredBadge, Demo, Err, Ok } from "../../components/ui.jsx";
import VerificationBadge from "../../components/VerificationBadge.jsx";

const ACTIONS = [["interested", "Interested"], ["info_requested", "Need more info"], ["declined", "Not a fit"]];

export default function CompanyInbox() {
  const [list, setList] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [err, setErr] = useState("");
  const load = () => api.inbox().then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  // Open the newest pitch straight away instead of an empty pane.
  useEffect(() => { if (list?.length && !openId) setOpenId(list[0].id); }, [list, openId]);

  if (!list) return <><Err msg={err} /><p className="text-slate-500">Loading inbox…</p></>;
  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div className="w-full shrink-0 space-y-2 lg:w-80">
        {list.length === 0 && (
          <div className="card text-center text-sm text-ink-500">
            <p className="font-display text-base font-semibold text-ink-900">No pitches yet</p>
            <p className="mt-1">Pitches from NGOs appear here, screened for fit with your focus areas.</p>
          </div>
        )}
        {list.map((p) => (
          <button key={p.id} onClick={() => setOpenId(p.id)}
            className={`card w-full p-3 text-left text-sm hover:-translate-y-0.5 ${openId === p.id ? "ring-2 ring-ink-900" : ""}`}>
            <div className="font-semibold">{p.ngo_name}<Demo on={p.ngo_is_demo} /></div>
            <div className="truncate text-xs text-slate-600">{p.subject}</div>
            <div className="text-[11px] text-slate-400">{fmtDate(p.sent_at)} · <span className="badge bg-slate-100">{p.status}</span> · ask {inr(p.ask_inr)}</div>
          </button>
        ))}
      </div>
      <div className="min-w-0 flex-1">{openId ? <PitchDetail id={openId} onChange={load} /> : list.length > 0 && <p className="text-ink-500">Open a pitch.</p>}</div>
    </div>
  );
}

function PitchDetail({ id, onChange }) {
  const [d, setD] = useState(null);
  const [screen, setScreen] = useState(null);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState("");
  const [reply, setReply] = useState({ text: "", meta: null });
  const [promise, setPromise] = useState({ what: "", amount: "", due_date: "" });
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => api.companyPitch(id).then((x) => { setD(x); setScreen(x.screening); }).catch((e) => setErr(e.message));
  useEffect(() => { setD(null); setScreen(null); setAction(""); setReply({ text: "", meta: null }); setOk(false); setMsg(""); load(); }, [id]);
  if (!d) return <p className="text-slate-500">Loading…</p>;
  const p = d.pitch;

  const runScreen = async () => { setBusy(true); try { setScreen(await api.screen(id)); load(); } finally { setBusy(false); } };
  const draftReply = async (a) => { setAction(a); setBusy(true); try { const r = await api.draftReply(id, a); setReply({ text: r.draft, meta: r }); } finally { setBusy(false); } };
  const send = async () => {
    setErr("");
    try {
      const pr = promise.what.trim() ? { what: promise.what, amount: promise.amount ? Number(promise.amount) : null, due_date: promise.due_date || null } : null;
      await api.respond(id, { action, reply_text: reply.text, promise: pr });
      setMsg("Reply sent. The NGO sees it in its follow-up timeline."); load(); onChange();
    } catch (e) { setErr(e.message); }
  };
  const report = async () => {
    const reason = window.prompt("Why is this pitch unsolicited or inappropriate?");
    if (!reason) return;
    await api.reportPitch(id, reason); setMsg("Reported. This NGO can no longer pitch you and is hidden from your directory."); onChange();
  };

  return (
    <div className="space-y-3">
      <div className="card">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{p.subject}</h2>
            <div className="text-sm">{d.ngo.name}<Demo on={d.ngo.is_demo} /> · {d.ngo.district}, {d.ngo.state} · causes: {d.ngo.causes.join(", ")}</div>
            <div className="mt-1"><VerificationBadge v={d.ngo.verification} detail /></div>
          </div>
          <span className="badge bg-slate-100">{p.status}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1 text-xs">
          {d.ngo.credentials.map((c) => <span key={c.label} className="inline-flex items-center gap-1">{c.label}{c.value ? `: ${c.value}` : ""} <CredBadge source={c.source} /></span>)}
        </div>
        <pre className="mt-3 whitespace-pre-wrap font-sans text-sm">{p.body}</pre>
        <div className="mt-2 text-xs text-slate-500">Ask {inr(p.ask_inr)} · cited public filing rows <SourceChip refs={p.evidence_refs} title="Rows cited by the NGO" /></div>
      </div>

      <div className="card text-sm">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">Screener (advisory)</h3>
          <button className="btn-ghost" onClick={runScreen} disabled={busy}>{screen ? "Re-run" : "Run Screener"}</button>
        </div>
        {screen && (
          <div className="mt-2 grid gap-3 md:grid-cols-2">
            <div>
              <ModelTag status={screen.summary_mode === "llm" ? "rules_only" : "template"} producedBy={screen.produced_by} />
              <ol className="mt-1 list-decimal pl-5">{(screen.summary || []).map((l, i) => <li key={i}>{l}</li>)}</ol>
              <div className="mt-2 text-xs"><b>Missing documents:</b> {screen.missing_documents.join(", ") || "none"}</div>
              <div className="text-xs"><b>Present (as declared):</b> {screen.documents_present.join(", ") || "none"}</div>
              <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">{screen.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
            </div>
            <div>
              <div className="text-2xl font-bold">{screen.fit.total}<span className="text-sm text-slate-400">/100 fit</span></div>
              {Object.entries(screen.fit.components).map(([k, v]) => (
                <div key={k} className="mb-1 text-xs">
                  <div className="flex justify-between"><span>{k.replace("_", " ")} × {screen.fit.weights[k]}</span><span>{screen.fit.weighted[k]} pts</span></div>
                  <div className="h-1.5 rounded-full bg-slate-100"><div className="h-1.5 rounded-full bg-emerald-600" style={{ width: `${v * 100}%` }} /></div>
                </div>
              ))}
              <ul className="text-[11px] text-slate-600">{screen.fit.notes.map((n) => <li key={n}>• {n}</li>)}</ul>
              <p className="mt-1 text-[11px] text-slate-400">{screen.fit.scored_on}. {screen.trace.policy}</p>
            </div>
          </div>
        )}
      </div>

      <div className="card space-y-2 text-sm">
        <h3 className="font-semibold">Your decision</h3>
        <div className="flex flex-wrap gap-2">
          {ACTIONS.map(([k, label]) => <button key={k} className={action === k ? "btn-primary" : "btn-ghost"} onClick={() => draftReply(k)} disabled={busy}>{label}</button>)}
          <button className="btn-ghost ml-auto text-rose-700" onClick={report}>Report unsolicited / block</button>
        </div>
        {action && (
          <>
            {reply.meta && <ModelTag status={reply.meta.status} producedBy={reply.meta.produced_by} fallbackFrom={reply.meta.fallback_from} />}
            <ReviewBox problems={reply.meta?.review_problems} rejected={reply.meta?.rejected} />
            <textarea className="input h-32" value={reply.text} onChange={(e) => setReply({ ...reply, text: e.target.value })} placeholder="Optional reply to the NGO" />
            {action !== "declined" && (
              <div className="grid gap-2 sm:grid-cols-3">
                <input className="input" placeholder="Promise: what (optional)" value={promise.what} onChange={(e) => setPromise({ ...promise, what: e.target.value })} />
                <input className="input" type="number" placeholder="Amount INR (optional)" value={promise.amount} onChange={(e) => setPromise({ ...promise, amount: e.target.value })} />
                <input className="input" type="date" value={promise.due_date} onChange={(e) => setPromise({ ...promise, due_date: e.target.value })} />
              </div>
            )}
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I reviewed this reply and want it sent</label>
            <button className="btn-primary" disabled={!ok} onClick={send}>Send reply</button>
          </>
        )}
        <Err msg={err} /><Ok msg={msg} />
      </div>

      <div className="card text-xs">
        <h3 className="mb-1 font-semibold">Thread</h3>
        {d.events.map((e) => (
          <div key={e.id} className="border-b py-1 last:border-0">
            <span className="text-slate-400">{fmtDate(e.created_at)}</span> <span className="badge bg-slate-100">{e.type}</span> {e.payload.headline || (typeof e.payload.summary === "string" ? e.payload.summary : "") || e.payload.text || ""}
          </div>
        ))}
      </div>
    </div>
  );
}
