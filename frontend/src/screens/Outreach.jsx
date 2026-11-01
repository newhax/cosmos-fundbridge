import { useEffect, useState } from "react";
import { api } from "../api.js";
import ModelTag, { ReviewBox } from "../components/ModelTag.jsx";
import { SourceChip } from "../components/Sources.jsx";

export default function Outreach({ run, me, selected, setSelected }) {
  const [pkg, setPkg] = useState(null);
  const [tone, setTone] = useState("formal");
  const [busy, setBusy] = useState(false);
  const [approved, setApproved] = useState(false);
  const [msg, setMsg] = useState("");
  const [done, setDone] = useState({});
  const [to, setTo] = useState("");
  const [match, setMatch] = useState(null);
  const [inAppOk, setInAppOk] = useState(false);
  const [pitched, setPitched] = useState(null);

  useEffect(() => {
    api.listOutreach().then((list) => setDone(Object.fromEntries(list.map((p) => [p.company_id, p.approved])))).catch(() => {});
  }, [pkg?.approved]);

  useEffect(() => {
    setPkg(null); setApproved(false); setMsg(""); setTo(""); setMatch(null); setInAppOk(false); setPitched(null);
    if (!selected) return;
    setBusy(true);
    api.outreach(selected).then((p) => { setPkg(p); setTo(p.to || ""); }).catch((e) => setMsg(e.message)).finally(() => setBusy(false));
    api.platformMatch(selected).then((r) => setMatch(r.match)).catch(() => setMatch(null));
  }, [selected]);

  if (!run) return <p className="text-slate-500">Run the pipeline from Onboarding first.</p>;
  const draft = pkg?.drafts.find((d) => d.tone === tone);

  const edit = (field) => (e) =>
    setPkg({ ...pkg, drafts: pkg.drafts.map((d) => (d.tone === tone ? { ...d, [field]: e.target.value } : d)) });
  const regenerate = async () => {
    setBusy(true);
    try { setPkg(await api.outreach(selected, true)); } finally { setBusy(false); }
  };
  const save = async () => { setPkg(await api.saveDrafts(selected, pkg.drafts)); setMsg("Edits saved."); };
  const copy = async () => {
    await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
    setMsg("Copied to clipboard.");
  };
  const mailto = () => `mailto:${encodeURIComponent(to.trim())}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
  const sendInApp = async () => {
    try {
      await api.saveDrafts(selected, pkg.drafts);
      const refs = [...new Set(pkg.facts_used.flatMap((f) => f.source_refs).filter((r) => !r.startsWith("query:")))];
      const p = await api.sendPitch({ company_id: match.company_id, subject: draft.subject, body: draft.body, evidence_refs: refs });
      setPitched(p);
      setMsg(`Pitch sent in app to ${match.name}. Replies will appear in Follow-ups automatically.`);
    } catch (e) { setMsg(e.message); }
  };
  const markSent = async () => {
    await api.saveDrafts(selected, pkg.drafts);
    await api.markSent(selected, draft);
    setPkg({ ...pkg, approved: true });
    setMsg("Marked as sent and remembered by the Follow-up Tracker.");
  };
  const words = draft ? draft.body.trim().split(/\s+/).length : 0;

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div className="w-full shrink-0 lg:w-64">
        <div className="card p-2">
          <div className="label px-2 pt-1">Top funders</div>
          {run.ranked.slice(0, 12).map((r) => (
            <button key={r.company_id} onClick={() => setSelected(r.company_id)}
              className={`flex w-full items-center justify-between gap-2 rounded-box px-2.5 py-2 text-left text-sm transition hover:bg-ink-50 ${selected === r.company_id ? "bg-brand-50 font-semibold text-brand-800" : ""}`}>
              <span className="truncate">{r.rank}. {r.company_name}</span>
              {done[r.company_id] === true && <span className="badge bg-emerald-100 text-emerald-800">sent</span>}
              {done[r.company_id] === false && <span className="badge bg-slate-100 text-slate-600">draft</span>}
            </button>
          ))}
        </div>
      </div>
      <div className="min-w-0 flex-1">
        {!selected && <p className="text-slate-500">Pick a company to draft an email.</p>}
        {busy && <p className="text-slate-500">Writer agent drafting and fact-checking…</p>}
        {pkg && draft && (
          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{pkg.company_name}</h2>
              <div className="flex gap-1">
                {["formal", "warm"].map((t) => (
                  <button key={t} onClick={() => setTone(t)} className={tone === t ? "btn-primary" : "btn-ghost"}>{t}</button>
                ))}
                <button className="btn-ghost" onClick={regenerate} disabled={busy || pkg.approved}>Regenerate</button>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              Writer: <ModelTag status={pkg.status} producedBy={pkg.produced_by} fallbackFrom={pkg.fallback_from} />
            </div>
            {pkg.fallback_from && (
              <p className="text-xs text-orange-800">Claude was not used ({pkg.fallback_from}); the draft was produced by {pkg.produced_by || "the template"}.</p>
            )}
            <ReviewBox problems={pkg.review_problems} rejected={pkg.rejected} />
            <TranslateBox companyId={selected} tone={tone} />
            <div className="rounded-box border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">⚠ {pkg.contact_note}</div>
            <div><label className="label">To</label><input className="input" value={to} onChange={(e) => setTo(e.target.value)} placeholder="CSR team email (add manually)" disabled={pkg.approved} /></div>
            <div><label className="label">Subject</label><input className="input" value={draft.subject} onChange={edit("subject")} disabled={pkg.approved} /></div>
            <div>
              <label className="label">Body <span className={words > 180 ? "text-red-600" : "text-slate-400"}>({words} words, max 180)</span>
                <span className="badge ml-2 bg-violet-100 text-violet-800">{draft.mode === "llm" ? `AI draft by ${draft.produced_by}` : "template"}</span></label>
              <textarea className="input h-72 font-sans" value={draft.body} onChange={edit("body")} disabled={pkg.approved} />
            </div>
            <div>
              <div className="label">Facts used (from public filings)</div>
              {pkg.facts_used.map((f, i) => <p key={i} className="text-xs text-slate-600">• {f.text}<SourceChip refs={f.source_refs} title="Rows behind this fact" /></p>)}
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t pt-3">
              <button className="btn-ghost" onClick={save} disabled={pkg.approved}>Save edits</button>
              <button className="btn-ghost" onClick={copy}>Copy</button>
              <a className="btn-ghost" href={mailto()}>Open in email app</a>
              <label className="ml-auto flex items-center gap-1 text-sm">
                <input type="checkbox" checked={approved || pkg.approved} onChange={(e) => setApproved(e.target.checked)} disabled={pkg.approved} />
                I reviewed this and sent it myself
              </label>
              <button className="btn-primary" disabled={!approved || pkg.approved} onClick={markSent}>
                {pkg.approved ? "Marked as sent ✓" : "Mark as sent"}
              </button>
            </div>
            <p className="text-[11px] text-slate-400">FundBridge never sends email. "Mark as sent" only records the event for follow-ups.</p>
            <div className="rounded-box border border-emerald-200 bg-emerald-50 p-3 text-sm">
              {match ? (
                <>
                  <div className="font-semibold">{match.name} is on FundBridge{match.is_demo && <span className="badge ml-1 bg-amber-300 text-amber-950">DEMO</span>}</div>
                  <p className="text-xs text-slate-600">Send this pitch inside the app. The company sees the pitch, your profile and the cited public rows, never your ranking or other pitches.</p>
                  {!me?.email_verified && <p className="text-xs text-sky-800">Verify your email first.</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-1 text-sm">
                      <input type="checkbox" checked={inAppOk} onChange={(e) => setInAppOk(e.target.checked)} disabled={!!pitched} />
                      I reviewed this pitch and want it sent
                    </label>
                    <button className="btn-primary" disabled={!inAppOk || !!pitched} onClick={sendInApp}>{pitched ? "Sent in app ✓" : "Send pitch in app"}</button>
                  </div>
                </>
              ) : (
                <p className="text-xs text-slate-600">This company is not on FundBridge. Reach it only with your own email (copy or open in your email app above). FundBridge does not collect contact data.</p>
              )}
            </div>
          </div>
        )}
        {msg && <p className="mt-2 text-sm text-emerald-800">{msg}</p>}
      </div>
    </div>
  );
}

// Machine translation of the current draft (drafts only). Names, IDs, numbers and dates are protected and
// checked; a translation that changes or adds any of them is rejected and the English draft stays as it is.
function TranslateBox({ companyId, tone }) {
  const [lang, setLang] = useState("hi");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const LANGS = { hi: "हिन्दी", ta: "தமிழ்", te: "తెలుగు", bn: "বাংলা", mr: "मराठी", gu: "ગુજરાતી", kn: "ಕನ್ನಡ", ml: "മലയാളം", or: "ଓଡ଼ିଆ", pa: "ਪੰਜਾਬੀ", as: "অসমীয়া" };
  const go = async () => {
    setBusy(true); setErr(""); setRes(null);
    try { setRes(await api.translateDraft(companyId, lang, tone)); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <details className="rounded-box border border-ink-200 p-3 text-xs">
      <summary className="cursor-pointer font-medium">Translate this draft</summary>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="tr-lang">Language</label>
        <select id="tr-lang" className="input w-40 py-1.5" value={lang} onChange={(e) => setLang(e.target.value)}>
          {Object.entries(LANGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button className="btn-ghost min-h-[34px] text-xs" onClick={go} disabled={busy}>{busy ? "Translating…" : "Translate draft"}</button>
        <span className="text-ink-500">Names, IDs, amounts and dates are kept exactly; the result is checked before it is shown.</span>
      </div>
      {err && <p className="mt-2 text-red-700">{err}</p>}
      {res && res.status !== "translated" && <p className="mt-2 text-sand-900">{res.reason}</p>}
      {res?.status === "translated" && (
        <div className="mt-2 space-y-1 rounded-box bg-ink-50 p-3" lang={res.lang}>
          <p className="badge bg-sand-100 text-sand-900">{res.label}</p>
          <p className="font-semibold">{res.subject}</p>
          <p className="whitespace-pre-line">{res.body}</p>
          <button className="btn-ghost min-h-[30px] px-3 py-1 text-xs" onClick={() => navigator.clipboard?.writeText(`${res.subject}\n\n${res.body}\n\n(${res.label})`)}>Copy translation</button>
        </div>
      )}
    </details>
  );
}
