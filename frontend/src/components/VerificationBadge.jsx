// Entity-level verification badge from the server's automatic checks. It never says "verified":
// nobody checks official records by hand.
const TONE = {
  good: "bg-emerald-600 text-white",
  info: "bg-sky-100 text-sky-800",
  neutral: "bg-slate-200 text-slate-700",
  warn: "bg-amber-200 text-amber-900",
  bad: "bg-rose-600 text-white",
};

export default function VerificationBadge({ v, detail = false }) {
  if (!v?.badge) return null;
  const b = v.badge;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className={`badge self-start ${TONE[b.tone] || TONE.neutral}`} title={`${b.text} ${b.disclaimer || ""}`}>{b.label}</span>
      {detail && (
        <span className="text-[11px] text-slate-600">
          {b.text} {b.disclaimer && <i className="text-slate-500">{b.disclaimer}</i>}
        </span>
      )}
      {detail && v.warnings?.map((w) => <span key={w} className="text-[11px] text-rose-700">⚠ {w}</span>)}
    </span>
  );
}
