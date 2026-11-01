import { createContext, useContext, useState } from "react";
import { api, inr } from "../api.js";

const SourcesCtx = createContext(() => {});
export const useSources = () => useContext(SourcesCtx);

/** Provider + modal that shows the underlying filing rows for a set of source_refs. */
export function SourcesProvider({ children }) {
  const [open, setOpen] = useState(null); // {title, refs, rows, loading}

  const show = async (refs, title = "Source rows") => {
    const real = refs.filter((r) => !r.startsWith("query:"));
    setOpen({ title, refs, rows: [], loading: true, queryOnly: real.length === 0 });
    const rows = real.length ? await api.rows(real) : [];
    setOpen({ title, refs, rows, loading: false, queryOnly: real.length === 0 });
  };

  return (
    <SourcesCtx.Provider value={show}>
      {children}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setOpen(null)}>
          <div className="max-h-[80vh] w-full max-w-5xl overflow-auto rounded-card bg-white p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-semibold">{open.title}</h3>
              <button className="btn-ghost" onClick={() => setOpen(null)}>Close</button>
            </div>
            {open.loading && <p className="text-sm text-slate-500">Loading…</p>}
            {open.queryOnly && (
              <p className="text-sm text-slate-600">
                This statement is about the <b>absence</b> of matching rows. It is backed by the dataset query shown in
                the decision trace, not by a specific row.
              </p>
            )}
            {!open.loading && open.rows.length > 0 && (
              <table className="w-full text-left text-xs">
                <thead className="border-b text-slate-500">
                  <tr>
                    <th className="py-1 pr-2">source_ref</th><th className="pr-2">Company (as filed)</th><th className="pr-2">FY</th>
                    <th className="pr-2">Development area (raw → mapped)</th><th className="pr-2">State (raw)</th>
                    <th className="pr-2">District</th><th className="pr-2 text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {open.rows.map((r) => (
                    <tr key={r.source_ref} className="border-b last:border-0">
                      <td className="py-1 pr-2 font-mono text-[11px] text-slate-500">{r.source_ref}</td>
                      <td className="pr-2">{r.company_name_raw}</td>
                      <td className="pr-2">{r.fy}</td>
                      <td className="pr-2">{r.sector_raw} → <b>{r.sector}</b></td>
                      <td className="pr-2">
                        {r.states_raw || <i className="text-slate-400">blank</i>}
                        {r.state_quality !== "ok" && <span className="badge ml-1 bg-amber-100 text-amber-800">{r.state_quality}</span>}
                      </td>
                      <td className="pr-2">{r.district || "—"}</td>
                      <td className="pr-2 text-right">{inr(r.amount_inr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="mt-3 text-[11px] text-slate-400">{open.rows.length} row(s). Rows come straight from the ingested CSV; line numbers refer to the original file.</p>
          </div>
        </div>
      )}
    </SourcesCtx.Provider>
  );
}

export function SourceChip({ refs, title }) {
  const show = useSources();
  if (!refs?.length) return null;
  return (
    <button
      onClick={() => show(refs, title)}
      className="badge ml-1 bg-mist-100 text-mist-800 transition hover:bg-mist-200"
      title={refs.join("\n")}
    >
      {refs[0].startsWith("query:") ? "dataset query" : `${refs.length} source${refs.length > 1 ? "s" : ""}`}
    </button>
  );
}
