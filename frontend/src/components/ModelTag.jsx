const STATUS = {
  verified: ["bg-emerald-100 text-emerald-800", "AI, cross-checked"],
  rules_only: ["bg-amber-100 text-amber-800", "AI, rule-checked only (model verifier unavailable)"],
  needs_human_review: ["bg-rose-600 text-white", "Needs human review"],
  unavailable: ["bg-slate-200 text-slate-700", "Template (no model available)"],
  template: ["bg-slate-200 text-slate-700", "Template"],
  none: ["bg-slate-200 text-slate-700", "Template"],
};

/** Shows who produced a text, whether it was a fallback, and its verification status. */
export default function ModelTag({ status, producedBy, fallbackFrom }) {
  const [cls, label] = STATUS[status] || STATUS.template;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={`badge ${cls}`}>{label}</span>
      {producedBy && <span className="badge bg-violet-100 font-mono text-violet-800" title="Model that actually produced this">{producedBy}</span>}
      {fallbackFrom && <span className="badge bg-orange-100 text-orange-800" title={fallbackFrom}>fallback: {fallbackFrom.split(" (")[0]} skipped</span>}
    </span>
  );
}

export function ReviewBox({ problems, rejected }) {
  if (!problems?.length) return null;
  return (
    <div className="rounded-box border border-rose-300 bg-rose-50 p-2 text-xs text-rose-900">
      <b>AI output rejected twice by the fact check. Showing the evidence-only template instead.</b>
      <ul className="mt-1 list-disc pl-5">{problems.slice(0, 6).map((p, i) => <li key={i}>{p}</li>)}</ul>
      {rejected && (
        <details className="mt-1"><summary className="cursor-pointer">Rejected AI text (for human review)</summary>
          <pre className="mt-1 whitespace-pre-wrap text-[11px]">{typeof rejected === "string" ? rejected : JSON.stringify(rejected, null, 1)}</pre>
        </details>
      )}
    </div>
  );
}
