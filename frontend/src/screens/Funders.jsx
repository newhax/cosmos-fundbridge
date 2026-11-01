import { useState } from "react";
import { api, inr } from "../api.js";
import ModelTag, { ReviewBox } from "../components/ModelTag.jsx";
import { CoverageBadge } from "../components/shell.jsx";
import { SourceChip, useSources } from "../components/Sources.jsx";

const LABELS = {
  sector_match: "Sector focus",
  state_match: "State focus",
  district_overlap: "District overlap",
  consistency_over_years: "Consistency over years",
  ticket_size_fit: "Ticket-size fit",
};

export default function Funders({ run, onOutreach }) {
  const [openId, setOpenId] = useState(null);
  if (!run) return <p className="text-slate-500">Run the pipeline from Onboarding first.</p>;
  const open = run.ranked.find((r) => r.company_id === openId);

  return (
    <div>
    <div className="flex flex-col gap-4 xl:flex-row">
      <div className="min-w-0 flex-1">
        <div className="mb-3 text-sm text-slate-600">
          Sectors: <b>{run.sectors.join(", ")}</b> <span className="text-xs text-slate-400">({run.sector_mode}: {run.sector_rationale})</span>
          <span className="ml-3 text-xs text-slate-400">{run.ranked.length} candidates · ranked in {run.duration_s}s{run.explained_after_s ? ` · explained in ${run.explained_after_s}s` : ""}{run.trace.query.dataset ? ` · dataset v${run.trace.query.dataset.version}` : ""}</span>
        </div>
        <CoverageBox run={run} />
        {run.explanations === "pending" && (
          <div className="mb-3 flex items-center gap-2.5 rounded-box border border-brand-200 bg-brand-50 px-4 py-2.5 text-xs text-brand-800">
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand-300 border-t-brand-700" aria-hidden />
            Ranking ready in {run.duration_s}s. Writing and fact-checking AI explanations for the top companies… the scores will not change.
          </div>
        )}
        {run.explanations === "failed" && (
          <div className="mb-3 rounded-box border border-sand-300 bg-sand-50 px-4 py-2.5 text-xs text-sand-900">
            AI explanations could not be written this time; the evidence-only explanations below are shown instead.
          </div>
        )}
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-ink-200 bg-ink-50">
              <tr><th className="px-4 py-3">#</th><th>Company</th><th title="Raw score (0-100) and, beside it, relative to the best match in your own list">Score · rel.</th><th>Why (short)</th><th className="text-center">Sources</th><th className="text-center">Unknowns</th><th /></tr>
            </thead>
            <tbody>
              {run.ranked.map((r) => (
                <tr key={r.company_id} onClick={() => setOpenId(r.company_id)}
                  className={`cursor-pointer border-b border-ink-100 transition-colors last:border-0 hover:bg-brand-50/60 ${openId === r.company_id ? "bg-brand-50" : ""}`}>
                  <td className="px-4 py-3 font-display font-semibold text-ink-400">{r.rank}</td>
                  <td className="font-medium">{r.company_name}{r.candidate.caveats.length > 0 && <span className="badge ml-1 bg-amber-100 text-amber-800" title={r.candidate.caveats.join("\n")}>caveats</span>}</td>
                  <td className="whitespace-nowrap"><ScorePill v={r.score.total} />
                    {r.score.relative != null && <span className="ml-1 text-[11px] text-ink-400" title="Relative to the best match in your list">{r.score.relative}</span>}</td>
                  <td className="max-w-md py-2 pr-2 text-xs text-slate-600">
                    {r.short_why}{" "}
                    {r.explanation_status && r.explanation_status !== "template" && <ModelTag status={r.explanation_status} producedBy={r.produced_by} />}
                  </td>
                  <td className="text-center"><SourceChip refs={r.sources} title={`${r.company_name}: all evidence rows`} /></td>
                  <td className="text-center text-xs text-slate-500">{r.unknowns.length}</td>
                  <td className="pr-3"><button className="btn-ghost text-xs" onClick={(e) => { e.stopPropagation(); onOutreach(r.company_id); }}>Draft email →</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {open && <TracePanel run={run} r={open} onClose={() => setOpenId(null)} onOutreach={onOutreach} />}
      </div>
      {run.fallback && <Fallback fb={run.fallback} />}
    </div>
  );
}

// Coverage of the NGO's area in the filings (GOOD / THIN / NONE) with the counts, and the weak-evidence warning.
function CoverageBox({ run }) {
  const c = run.coverage;
  if (!c) return null;
  const st = c.state || {};
  return (
    <div className={`mb-3 flex flex-wrap items-center gap-2 rounded-box px-4 py-2.5 text-xs ${c.level === "GOOD" ? "bg-white text-ink-600" : "bg-sand-50 text-sand-900"}`}>
      <span className="font-medium">Coverage of your area:</span> <CoverageBadge level={c.level} />
      {st.n_companies != null && <span>{st.n_companies} companies · {st.n_years} financial years · {st.n_rows} rows in these sectors (state level)</span>}
      {c.district && <span>· district: <CoverageBadge level={c.district.level} /></span>}
      {c.warning && <b className="w-full">{c.warning}</b>}
      {run.geo_version && <span className="text-ink-400">geo v{run.geo_version}</span>}
    </div>
  );
}

// Fallback evidence for thin areas: two labelled lists, lower weights, never mixed into the ranking above.
function Fallback({ fb }) {
  const block = (title, part) => part && (
    <section className="card space-y-2 text-sm" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{title}</h3><span className="badge bg-sand-100 text-sand-900">{part.label}</span></div>
      {part.basis && <p className="text-xs text-ink-500">{part.basis}{part.states?.length ? `: ${part.states.map((s) => `${s.name} (similarity ${s.similarity})`).join(", ")}` : ""}</p>}
      {(!part.funders || part.funders.length === 0) ? <p className="text-xs text-ink-500">None found.</p> : (
        <ul className="divide-y divide-ink-100 text-xs">
          {part.funders.map((f) => (
            <li key={f.company_id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
              <span><b>{f.company_name}</b> · {inr(f.spend_inr)} · {f.n_years} yrs <span className="text-ink-400">({f.reason})</span></span>
              <span className="badge bg-ink-100 text-ink-700" title={`Weight ${f.weight}: shown lower than local matches`}>{f.fallback_score} · weight {f.weight}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      {block("Funders in similar states", fb.similar_states)}
      {block("Pan-India funders", fb.pan_india)}
    </div>
  );
}

function ScorePill({ v }) {
  // Text/background pairs meet WCAG AA contrast (4.5:1).
  const c = v >= 75 ? "bg-emerald-700 text-white" : v >= 55 ? "bg-emerald-100 text-emerald-900" : "bg-ink-200 text-ink-800";
  return <span className={`badge ${c}`}>{v.toFixed(1)}</span>;
}

function TracePanel({ run, r, onClose, onOutreach }) {
  const show = useSources();
  const t = run.trace;
  const next = run.ranked.find((x) => x.rank === r.rank + 1);
  const prev = run.ranked.find((x) => x.rank === r.rank - 1);
  const pairDown = t.pairwise.find((p) => p.higher_rank === r.rank);
  const pairUp = t.pairwise.find((p) => p.lower_rank === r.rank);
  const c = r.candidate;
  const companyCaveats = t.data_quality_caveats.filter((x) => x.startsWith(r.company_name + ":"));
  const globalCaveats = t.data_quality_caveats.filter((x) => !run.ranked.some((y) => x.startsWith(y.company_name + ":")));

  return (
    <aside className="w-full shrink-0 animate-fade-up rounded-card border border-ink-200/70 bg-white p-5 shadow-lift xl:sticky xl:top-24 xl:h-[calc(100vh-8rem)] xl:w-[460px] xl:overflow-y-auto">
      <div className="mb-2 flex items-start justify-between">
        <div>
          <div className="text-xs uppercase text-slate-400">Decision trace · rank {r.rank}</div>
          <h3 className="text-lg font-semibold">{r.company_name}</h3>
          <div className="text-xs text-slate-400">{r.company_id}{c.cin ? "" : " (no CIN)"}</div>
        </div>
        <button className="btn-ghost" onClick={onClose}>×</button>
      </div>

      <Section title={`Score ${r.score.total.toFixed(1)} / 100`}>
        {Object.entries(r.score.components).map(([k, v]) => (
          <div key={k} className="mb-1.5">
            <div className="flex justify-between text-xs"><span>{LABELS[k]} <span className="text-slate-400">× {r.score.weights[k]}</span></span>
              <span className="font-mono">{(v * 100).toFixed(0)}% → {r.score.weighted[k].toFixed(1)} pts</span></div>
            <div className="h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-emerald-600" style={{ width: `${v * 100}%` }} /></div>
          </div>
        ))}
        <p className="mt-1 text-[11px] text-slate-400">Deterministic formula; weights from config. No LLM involved in scoring.</p>
      </Section>

      <Section title="Why (every sentence is sourced)">
        {r.explanation.map((s, i) => (
          <p key={i} className="mb-1.5 text-sm">{s.text}<SourceChip refs={s.source_refs} title="Rows behind this sentence" /></p>
        ))}
        <div className="mt-1"><ModelTag status={r.explanation_status || "template"} producedBy={r.produced_by} fallbackFrom={r.fallback_from} /></div>
        <p className="mt-1 text-[11px] text-slate-400">
          {r.explanation_mode === "llm"
            ? "Written by the model from computed facts; passed rule checks and a second-model claim check against the evidence."
            : r.rank > 5 ? "Evidence-only template (only the top 5 are AI-explained, to save free-tier quota)." : "Evidence-only template."}
        </p>
        <ReviewBox problems={r.review_problems} rejected={r.rejected_text?.join("\n")} />
      </Section>

      <Section title="Raw numbers">
        <Kv k="Sector + state spend" v={inr(c.sector_state_spend)} />
        <Kv k="Sector spend, all states" v={inr(c.sector_spend_any_state)} />
        <Kv k="Total CSR spend (window)" v={inr(c.total_spend)} />
        <Kv k="District spend (any sector)" v={inr(c.district_spend)} />
        <Kv k="Multi-state / PAN India (not attributed)" v={inr(c.multi_state_sector_spend)} />
        <Kv k="Years funded (sector + state)" v={`${c.years_funded} of ${c.years_window}`} />
        <Kv k="Avg ticket in sector" v={inr(c.avg_ticket_sector)} />
        <Kv k="Trend" v={c.trend} />
        <div className="mt-2 flex items-end gap-1">
          {Object.entries(c.per_year_sector_state).map(([fy, v]) => {
            const max = Math.max(...Object.values(c.per_year_sector_state), 1);
            return (
              <div key={fy} className="flex flex-1 flex-col items-center" title={`${fy}: ${inr(v)}`}>
                <div className="w-full rounded-t bg-sky-500" style={{ height: `${(v / max) * 48 + 1}px` }} />
                <div className="mt-0.5 text-[9px] text-slate-400">{fy}</div>
              </div>
            );
          })}
        </div>
        <button className="btn-ghost mt-2 text-xs" onClick={() => show(r.sources, `${r.company_name}: evidence rows`)}>
          View {r.sources.length} source rows
        </button>
      </Section>

      {(pairUp || pairDown) && (
        <Section title="Why this ranking (pairwise)">
          {pairUp && <Pair p={pairUp} label={`Why ${prev?.company_name} is above`} />}
          {pairDown && <Pair p={pairDown} label={`Why this outranked ${next?.company_name}`} />}
        </Section>
      )}

      <Section title="Unknown (not in public data)">
        <ul className="list-disc pl-5 text-sm text-slate-600">{r.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
      </Section>

      <Section title="Data-quality caveats">
        {companyCaveats.length === 0 && c.caveats.length === 0 && <p className="text-xs text-slate-500">None specific to this company.</p>}
        <ul className="list-disc pl-5 text-xs text-amber-800">
          {(companyCaveats.length ? companyCaveats.map((x) => x.slice(r.company_name.length + 2)) : c.caveats).map((x) => <li key={x}>{x}</li>)}
        </ul>
        <details className="mt-1 text-xs text-slate-500"><summary className="cursor-pointer">Dataset-wide caveats</summary>
          <ul className="list-disc pl-5">{globalCaveats.map((x) => <li key={x}>{x}</li>)}</ul></details>
      </Section>

      <Section title="Query used">
        <details className="text-xs"><summary className="cursor-pointer text-slate-600">SQL{t.query.dataset ? ` (dataset v${t.query.dataset.version})` : ""} · FY {t.query.fy_window?.[0]} to {t.query.fy_window?.at(-1)} · {t.query.candidates_considered} companies matched</summary>
          <pre className="mt-1 overflow-x-auto rounded-box bg-ink-900 p-3 text-[10px] text-ink-100">{t.query.sql}</pre>
          <pre className="mt-1 overflow-x-auto rounded-box bg-ink-100 p-3 text-[10px]">{JSON.stringify(t.query.params, null, 1)}</pre>
        </details>
        <p className="mt-1 text-[11px] text-slate-400">Trace {t.trace_id} · run {t.run_id} · logged to memory</p>
      </Section>

      <DataProvenance run={run} />

      <Section title={`Model calls this run (${t.llm_calls?.length || 0})`}>
        {!t.llm_calls?.length && <p className="text-xs text-slate-500">None: offline templates or user-chosen sectors.</p>}
        {t.llm_calls?.length > 0 && (
          <table className="w-full text-[11px]">
            <thead className="text-slate-400"><tr><th className="text-left">role</th><th className="text-left">model</th><th className="text-right">ms</th><th className="text-right">tokens in/out</th><th /></tr></thead>
            <tbody>
              {t.llm_calls.map((c, i) => (
                <tr key={i} className="border-t" title={c.fallback_from ? `fallback from ${c.fallback_from}` : ""}>
                  <td>{c.role}</td><td className="font-mono">{c.provider}:{c.model}</td>
                  <td className="text-right">{c.latency_ms}</td><td className="text-right">{c.input_tokens}/{c.output_tokens}</td>
                  <td className="pl-1">{c.cached ? <span className="badge bg-sky-100 text-sky-800">cache</span> : c.outcome !== "ok" ? <span className="badge bg-rose-100 text-rose-800">{c.outcome}</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <button className="btn-primary w-full justify-center" onClick={() => onOutreach(r.company_id)}>Draft outreach email</button>
    </aside>
  );
}

function Pair({ p, label }) {
  return (
    <div className="mb-2 rounded-box border border-slate-200 p-2">
      <div className="text-xs font-semibold">{label}</div>
      <p className="text-xs text-slate-600">{p.summary}</p>
      <div className="mt-1 grid grid-cols-5 gap-1 text-center text-[10px]">
        {Object.entries(p.component_deltas).map(([k, v]) => (
          <div key={k} className={`rounded-box px-1 py-0.5 ${v > 0 ? "bg-emerald-100 text-emerald-800" : v < 0 ? "bg-rose-100 text-rose-800" : "bg-slate-100 text-slate-500"}`}>
            <div className="truncate">{LABELS[k]}</div><div className="font-mono">{v > 0 ? "+" : ""}{v.toFixed(1)}</div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-slate-400">Deltas in weighted points: {p.higher} minus {p.lower}.</p>
    </div>
  );
}

const Section = ({ title, children }) => (
  <section className="mb-5"><h4 className="mb-2 border-b border-ink-900/10 pb-2 text-sm font-semibold tracking-snug text-ink-900">{title}</h4>{children}</section>
);
const Kv = ({ k, v }) => (
  <div className="flex justify-between border-b border-dashed border-slate-100 py-0.5 text-xs"><span className="text-slate-500">{k}</span><span className="font-mono">{v}</span></div>
);

const shortId = (id) => (id ? String(id).replace(/^sha256:/, "").slice(0, 12) : "unknown");

function DataProvenance({ run }) {
  const p = run.trace.data_provenance;
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!p?.queries?.length || p.version == null) {
    return (
      <Section title="Data provenance">
        <p className="text-xs text-ink-500">Not recorded for this run (made before dataset versions were tracked). Run the funder search again.</p>
      </Section>
    );
  }
  const go = async () => {
    setBusy(true);
    try { setRes(await api.replayRun(run.run_id)); } catch (e) { setRes({ status: "unavailable", reason: e.message }); }
    setBusy(false);
  };
  const tone = { reproduced: "bg-sage-100 text-sage-900", differs: "bg-coral-100 text-coral-800", unavailable: "bg-sand-100 text-sand-900" };
  return (
    <Section title="Data provenance">
      <div className="space-y-2 text-xs">
        <p className="text-ink-600">Read from this dataset version and source file:</p>
        <dl className="grid gap-1">
          {[["Dataset", p.dataset_id], ["Version", `v${p.version}`], ["File sha256", p.sha256], ["Tables", p.tables?.join(", ")]].map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 rounded-box bg-ink-50 px-2.5 py-1.5">
              <dt className="text-ink-500">{k}</dt>
              <dd className="min-w-0 truncate text-right font-mono text-[11px]" title={v}>{k === "File sha256" ? shortId(v) : v}</dd>
            </div>
          ))}
        </dl>
        <details>
          <summary className="cursor-pointer text-ink-600">{p.queries.length} queries · rows {p.row_counts.join(" / ")}</summary>
          {p.queries.map((q, i) => (
            <div key={i} className="mt-1.5">
              <div className="text-[11px] text-ink-500">Q{i + 1} · {q.tables.join(", ")} · {q.row_count} rows · {shortId(q.result_hash)}</div>
              <pre className="mt-0.5 max-h-32 overflow-auto rounded-box bg-ink-900 p-2 text-[10px] text-ink-100">{q.sql}</pre>
            </div>
          ))}
        </details>
        {p.data_quality_caveats?.length > 0 && (
          <details><summary className="cursor-pointer text-ink-600">Data-quality caveats ({p.data_quality_caveats.length})</summary>
            <ul className="mt-1 list-disc pl-5 text-ink-600">{p.data_quality_caveats.map((c) => <li key={c}>{c}</li>)}</ul>
          </details>
        )}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button className="btn-ghost min-h-[34px] px-3 py-1 text-xs" onClick={go} disabled={busy}>
            {busy ? "Replaying…" : `Replay on dataset v${p.version}`}
          </button>
          {res && <span className={`badge ${tone[res.status] || tone.unavailable}`}>{res.status}</span>}
        </div>
        {res?.status === "reproduced" && (
          <p className="text-ink-600">All {res.queries_checked} queries returned the same rows and the ranking is identical.</p>
        )}
        {res?.status === "differs" && (
          <p className="text-coral-800">
            {res.queries_matching}/{res.queries_checked} queries match; ranking {res.ranking_matches ? "matches" : `differs at ${res.ranking_differences.length} position(s)`}.
          </p>
        )}
        {res?.status === "unavailable" && <p className="text-sand-900">{res.reason}</p>}
      </div>
    </Section>
  );
}
