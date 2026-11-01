// Small shared pieces: pagination, share links, feedback dialog, notifications bell, coverage badge,
// offline banner and the language / amount-unit controls.
import { useEffect, useRef, useState } from "react";
import { api, fmtDate, getUnit, setUnit } from "../api.js";
import { DICTS, useI18n } from "../i18n/index.jsx";

export function usePaged(items, perPage = 10) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil((items?.length || 0) / perPage));
  useEffect(() => { if (page > pages) setPage(1); }, [pages, page]);
  return { page, pages, setPage, slice: (items || []).slice((page - 1) * perPage, page * perPage) };
}

export function Pager({ page, pages, onPage }) {
  const { t } = useI18n();
  if (pages <= 1) return null;
  return (
    <nav className="flex items-center justify-between gap-2 pt-2 text-xs" aria-label="Pagination">
      <button className="btn-ghost min-h-[34px] px-3 py-1 text-xs" disabled={page <= 1} onClick={() => onPage(page - 1)}>← {t("common.previous")}</button>
      <span className="text-ink-500" aria-live="polite">{t("common.page", { page, pages })}</span>
      <button className="btn-ghost min-h-[34px] px-3 py-1 text-xs" disabled={page >= pages} onClick={() => onPage(page + 1)}>{t("common.next")} →</button>
    </nav>
  );
}

// Share text by WhatsApp or the clipboard. Only the text given is shared: never ids, emails or other personal data.
export function ShareLinks({ text, label = "Share" }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-xs">
      <span className="text-ink-500">{label}:</span>
      <a className="btn-ghost min-h-[32px] px-3 py-1 text-xs" target="_blank" rel="noopener noreferrer"
        href={`https://wa.me/?text=${encodeURIComponent(text)}`}>WhatsApp</a>
      <button type="button" className="btn-ghost min-h-[32px] px-3 py-1 text-xs" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </span>
  );
}

const CATS = ["bug", "idea", "data", "language", "other"];

export function FeedbackButton({ page, me }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ category: "idea", rating: "", message: "" });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const dialog = useRef(null);
  useEffect(() => { if (open) dialog.current?.querySelector("select")?.focus(); }, [open]);
  const send = async (e) => {
    e.preventDefault(); setErr("");
    const state = me?.profile?.hq_area?.state || "";
    try { await api.feedback({ ...f, page, language: lang, state }); setMsg(t("feedback.thanks")); setF({ ...f, message: "" }); }
    catch (e2) { setErr(e2.message); }
  };
  return (
    <>
      <button type="button" className="btn-on-dark min-h-[40px]" onClick={() => { setOpen(true); setMsg(""); }}>{t("feedback.button")}</button>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-labelledby="fb-title"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
          <form ref={dialog} onSubmit={send} className="w-full max-w-md space-y-3 rounded-card bg-white p-6 text-ink-900">
            <h2 id="fb-title" className="text-xl font-semibold">{t("feedback.title")}</h2>
            <div><label className="label" htmlFor="fb-cat">{t("feedback.category")}</label>
              <select id="fb-cat" className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
                {CATS.map((c) => <option key={c} value={c}>{t(`feedback.cat.${c}`)}</option>)}</select></div>
            <div><label className="label" htmlFor="fb-rating">{t("feedback.rating")}</label>
              <select id="fb-rating" className="input" value={f.rating} onChange={(e) => setF({ ...f, rating: e.target.value })}>
                <option value="">-</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"★".repeat(n)}</option>)}</select></div>
            <div><label className="label" htmlFor="fb-msg">{t("feedback.message")}</label>
              <textarea id="fb-msg" className="input" rows={4} minLength={5} maxLength={2000} required value={f.message}
                onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
            {err && <p className="text-sm text-red-700">{err}</p>}
            {msg && <p className="text-sm text-emerald-800" role="status">{msg}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>{t("common.close")}</button>
              <button className="btn-primary">{t("common.send")}</button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}

export function NotificationsBell({ onOpenTab }) {
  const { t } = useI18n();
  const [data, setData] = useState({ unread: 0, items: [] });
  const [open, setOpen] = useState(false);
  const load = () => api.notifications().then(setData).catch(() => {});
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, []);
  return (
    <div className="relative">
      <button type="button" aria-expanded={open} aria-label={`${t("notify.title")}: ${data.unread}`} onClick={() => setOpen((o) => !o)}
        className="relative grid h-10 w-10 place-items-center rounded-full text-white ring-1 ring-inset ring-white/20 hover:ring-white/50">
        <span aria-hidden>🔔</span>
        {data.unread > 0 && <span className="absolute -right-1 -top-1 grid h-5 min-w-[20px] place-items-center rounded-full bg-coral-600 px-1 text-[10px] font-bold">{data.unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 max-w-[90vw] rounded-card bg-white p-3 text-sm text-ink-900 shadow-lift">
          <div className="mb-2 flex items-center justify-between">
            <b>{t("notify.title")}</b>
            {data.unread > 0 && <button className="text-xs underline" onClick={() => api.notificationsRead().then(load)}>{t("notify.markread")}</button>}
          </div>
          <ul className="max-h-80 space-y-1 overflow-y-auto">
            {data.items.length === 0 && <li className="text-xs text-ink-500">{t("notify.none")}</li>}
            {data.items.map((n) => (
              <li key={n.id}>
                <button className={`w-full rounded-box px-2 py-1.5 text-left text-xs hover:bg-ink-50 ${n.read ? "text-ink-500" : "font-medium"}`}
                  onClick={() => { setOpen(false); if (n.tab) onOpenTab?.(n.tab); }}>
                  {n.text}<span className="block text-[10px] text-ink-400">{fmtDate(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const LEVEL_TONE = { GOOD: "bg-sage-100 text-sage-900", THIN: "bg-sand-100 text-sand-900", NONE: "bg-coral-100 text-coral-800",
  UNKNOWN: "bg-ink-100 text-ink-600" };

export function CoverageBadge({ level }) {
  const { t } = useI18n();
  const label = { GOOD: t("coverage.good"), THIN: t("coverage.thin"), NONE: t("coverage.none") }[level] || level || "unknown";
  return <span className={`badge ${LEVEL_TONE[level] || LEVEL_TONE.UNKNOWN}`} title="Coverage of the public filings">{label}</span>;
}

export function OfflineBanner() {
  const { t } = useI18n();
  const [offline, setOffline] = useState(typeof navigator !== "undefined" && !navigator.onLine);
  useEffect(() => {
    const on = () => setOffline(false); const off = () => setOffline(true);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  if (!offline) return null;
  return <div role="status" className="bg-sand-500 px-4 py-2 text-center text-xs font-medium text-ink-900">{t("common.offline")}</div>;
}

export function LangUnitControls({ me, languages, onUnit }) {
  const { t, lang, setLang } = useI18n();
  const [unit, setU] = useState(getUnit());
  const changeLang = (l) => { setLang(l); if (me) api.setLanguage(l).catch(() => {}); };
  const all = languages || { en: "English", hi: "हिन्दी" };
  return (
    <div className="flex items-center gap-2 text-xs">
      <label className="sr-only" htmlFor="lang-select">{t("common.language")}</label>
      <select id="lang-select" value={lang} onChange={(e) => changeLang(e.target.value)} title="Other languages need a translation file"
        className="w-28 rounded-full bg-white/10 px-2 py-1.5 text-white ring-1 ring-inset ring-white/20 [&_option]:bg-white [&_option]:text-ink-900 [&_optgroup]:bg-white [&_optgroup]:text-ink-500">
        <optgroup label="Available">
          {Object.entries(all).filter(([code]) => DICTS[code]).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </optgroup>
        <optgroup label="Coming (translation file needed)">
          {Object.entries(all).filter(([code]) => !DICTS[code]).map(([code, name]) => <option key={code} value={code} disabled>{name}</option>)}
        </optgroup>
      </select>
      <label className="sr-only" htmlFor="unit-select">{t("common.amounts")}</label>
      <select id="unit-select" value={unit} title={t("common.amounts")}
        onChange={(e) => { setU(e.target.value); setUnit(e.target.value); onUnit?.(e.target.value); }}
        className="rounded-full bg-white/10 px-2 py-1.5 text-white ring-1 ring-inset ring-white/20 [&_option]:bg-white [&_option]:text-ink-900 [&_optgroup]:bg-white [&_optgroup]:text-ink-500">
        {["auto", "rupees", "lakh", "crore"].map((u) => <option key={u} value={u}>₹ {t(`common.${u}`)}</option>)}
      </select>
    </div>
  );
}
