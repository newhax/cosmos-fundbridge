// National coverage of the public CSR filings: every state/UT from the loaded LGD file, then its districts.
// Public page (no login needed). Absent data does not mean there is no CSR activity.
import { useEffect, useMemo, useState } from "react";
import { api, inr } from "../api.js";
import { CoverageBadge, Pager, usePaged } from "../components/shell.jsx";
import { useI18n } from "../i18n/index.jsx";

export default function Coverage({ status }) {
  const { t } = useI18n();
  const [sector, setSector] = useState("");
  const [data, setData] = useState(null);
  const [sort, setSort] = useState("name");
  const [open, setOpen] = useState(null);
  useEffect(() => { api.coverage(sector).then(setData).catch(() => setData(null)); }, [sector]);
  const rows = useMemo(() => {
    const r = [...(data?.states || [])];
    const by = { name: (a, b) => a.name.localeCompare(b.name), spend: (a, b) => b.spend_inr - a.spend_inr,
      companies: (a, b) => b.n_companies - a.n_companies, level: (a, b) => "NONE THIN GOOD".indexOf(a.level) - "NONE THIN GOOD".indexOf(b.level) };
    return r.sort(by[sort]);
  }, [data, sort]);
  const counts = rows.reduce((m, r) => ({ ...m, [r.level]: (m[r.level] || 0) + 1 }), {});
  return (
    <div className="space-y-4">
      <p className="rounded-box bg-sand-50 px-4 py-2.5 text-sm text-sand-900">{t("coverage.absent")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div><label className="label" htmlFor="cv-sector">Sector</label>
          <select id="cv-sector" className="input w-56" value={sector} onChange={(e) => { setSector(e.target.value); setOpen(null); }}>
            <option value="">All sectors</option>{(status?.sectors || []).map((s) => <option key={s}>{s}</option>)}</select></div>
        <div><label className="label" htmlFor="cv-sort">Sort by</label>
          <select id="cv-sort" className="input w-48" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="name">Name</option><option value="spend">Reported spend</option><option value="companies">Companies</option><option value="level">Weakest coverage first</option></select></div>
        {data && <p className="text-xs text-ink-500">{rows.length} states/UTs from the LGD file (geo v{data.geo_version}) · GOOD {counts.GOOD || 0} · THIN {counts.THIN || 0} · NONE {counts.NONE || 0} ·
          {" "}{data.national.rows_all_india.toLocaleString("en-IN")} rows filed as All India are not attributed to any state.</p>}
      </div>
      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[720px] text-left text-sm">
          <caption className="sr-only">CSR filings coverage by state and union territory</caption>
          <thead className="border-b border-ink-200 bg-ink-50">
            <tr><th className="px-4 py-3">State / UT</th><th>Coverage</th><th className="text-right">Companies</th><th className="text-right">Years</th>
              <th className="text-right">Rows</th><th className="text-right">Reported spend</th><th className="text-right">No district</th><th className="pr-4" /></tr>
          </thead>
          <tbody>
            {!data && <tr><td colSpan={8} className="px-4 py-3 text-ink-500">{t("common.loading")}</td></tr>}
            {rows.map((r) => (
              <tr key={r.state_lgd} className="border-b border-ink-100">
                <td className="px-4 py-2 font-medium">{r.name}{r.kind === "ut" && <span className="ml-1 text-xs text-ink-400">UT</span>}</td>
                <td><CoverageBadge level={r.level} /></td>
                <td className="text-right tabular-nums">{r.n_companies.toLocaleString("en-IN")}</td>
                <td className="text-right tabular-nums">{r.n_years}</td>
                <td className="text-right tabular-nums">{r.n_rows.toLocaleString("en-IN")}</td>
                <td className="text-right tabular-nums">{r.n_rows ? inr(r.spend_inr) : "unknown"}</td>
                <td className="text-right tabular-nums">{r.share_no_district == null ? "unknown" : `${Math.round(r.share_no_district * 100)}%`}</td>
                <td className="pr-4 text-right"><button className="btn-ghost min-h-[30px] px-3 py-1 text-xs" onClick={() => setOpen(r)}
                  aria-label={`Districts of ${r.name}`}>{r.n_districts} districts</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && <Districts state={open} sector={sector} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Districts({ state, sector, onClose }) {
  const [d, setD] = useState(null);
  useEffect(() => { api.coverageState(state.state_lgd, sector).then(setD).catch(() => setD(null)); }, [state.state_lgd, sector]);
  const { page, pages, setPage, slice } = usePaged(d?.districts || [], 15);
  return (
    <section className="card space-y-3 text-sm" aria-label={`Districts of ${state.name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{state.name}: districts</h2>
        <button className="btn-ghost min-h-[34px] text-xs" onClick={onClose}>Close</button>
      </div>
      {d?.portal && <p className="text-xs">State CSR portal: <a className="underline" href={d.portal.url} target="_blank" rel="noopener noreferrer">{d.portal.url}</a>
        <span className="text-ink-400"> ({d.portal.note})</span></p>}
      {d && <p className="text-xs text-ink-500">{d.districts_with_no_reported_projects.length} of {d.districts.length} districts have no reported projects in {d.sector}.</p>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead className="border-b border-ink-200"><tr><th className="py-2">District</th><th>Coverage</th><th className="text-right">Companies</th><th className="text-right">Rows</th><th className="text-right">Reported spend</th></tr></thead>
          <tbody>{slice.map((x) => (
            <tr key={x.district_lgd} className="border-b border-ink-100">
              <td className="py-1.5">{x.name}</td><td><CoverageBadge level={x.level} /></td>
              <td className="text-right tabular-nums">{x.n_companies}</td><td className="text-right tabular-nums">{x.n_rows}</td>
              <td className="text-right tabular-nums">{x.n_rows ? inr(x.spend_inr) : "unknown"}</td>
            </tr>))}</tbody>
        </table>
      </div>
      <Pager page={page} pages={pages} onPage={setPage} />
    </section>
  );
}
