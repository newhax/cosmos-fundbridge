// One NGO as companies see it in the directory. Used by the company directory and by the NGO's own
// "Directory preview", so the preview always matches the real listing (only the fields the NGO chose).
import { CredBadge, Demo } from "./ui.jsx";
import VerificationBadge from "./VerificationBadge.jsx";

export default function NgoListingCard({ n, className = "card" }) {
  const place = [n.district, n.state].filter(Boolean).join(", ");
  return (
    <div className={`${className} space-y-2 text-sm`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold">{n.name}<Demo on={n.is_demo} /></div>
          {place && <div className="text-xs text-ink-500">{place}</div>}
        </div>
        <VerificationBadge v={n.verification} />
      </div>
      {n.causes?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {n.causes.map((c) => <span key={c} className="badge bg-ink-100 text-ink-700">{c}</span>)}
        </div>
      )}
      {n.summary && <p className="text-xs leading-relaxed text-ink-700">{n.summary}</p>}
      {n.credentials?.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
          {n.credentials.map((c) => <span key={c.label}>{c.label}{c.value ? ` ${c.value}` : ""} <CredBadge source={c.source} /></span>)}
        </div>
      )}
      {n.verification?.badge?.text && <p className="text-[11px] text-ink-400">{n.verification.badge.text}</p>}
    </div>
  );
}
