"""CSRStore interface plus the SQL ranking logic over the versioned CSR dataset.

Queries are written with ":name" placeholders and "@table" tokens; the store resolves each token
against one dataset version (fundbridge/datasets.py).

Tables (built by fundbridge/ingest.py into every dataset version):
  gold_funders_sector_state  ranking input (spend by company x year x sector x state)
  gold_company_gap           reported gap (outlay - spend)
  clean_projects             evidence rows and source refs
  clean_companies            identity + data-quality counts
  fb_meta                    data source + data-quality report

Provenance: inside `with store.provenance():` every query records its SQL, params, tables, row count
and a result hash, and the run records the dataset it read (dataset_id, version, sha256), so a
decision trace can be replayed later on the same dataset version (see fundbridge/replay.py).
"""
from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from ..models import Candidate, Evidence

EVIDENCE_PER_COMPANY = 40
COVERAGE_WARNING = "Few filings for this area. Treat results as weak evidence."


def coverage_level(n_companies: int, n_years: int, district: bool = False) -> str:
    """GOOD / THIN / NONE from distinct companies and financial years (thresholds in config)."""
    from ..config import COVERAGE_GOOD, COVERAGE_GOOD_DISTRICT

    t = COVERAGE_GOOD_DISTRICT if district else COVERAGE_GOOD
    if not n_companies:
        return "NONE"
    return "GOOD" if n_companies >= t["companies"] and n_years >= t["years"] else "THIN"

_TABLE_RE = re.compile(r"@([a-z_]+)")
_PROVENANCE: ContextVar["Provenance | None"] = ContextVar("fb_csr_provenance", default=None)
_PINNED: ContextVar["dict | None"] = ContextVar("fb_csr_pinned", default=None)


def rows_hash(rows: list[dict]) -> str:
    """Order-independent hash of a query result (floats rounded so re-runs agree)."""
    def norm(v):
        return round(v, 2) if isinstance(v, float) else v

    lines = sorted(json.dumps({k: norm(v) for k, v in r.items()}, sort_keys=True, default=str) for r in rows)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


@dataclass
class Provenance:
    dataset: dict | None = None  # {dataset_id, version, sha256}: fixed at the first query of the run
    queries: list[dict] = field(default_factory=list)

    def summary(self, caveats: list[str]) -> dict:
        d = self.dataset or {}
        return {"dataset_id": d.get("dataset_id"), "version": d.get("version"), "sha256": d.get("sha256"),
                "tables": sorted({t for q in self.queries for t in q["tables"]}),
                "queries": list(self.queries), "row_counts": [q["row_count"] for q in self.queries],
                "data_quality_caveats": caveats}


def dataset_caveats(report: dict) -> list[str]:
    """Dataset-wide data-quality caveats, recorded with every trace."""
    out = []
    if report.get("data_source") == "SAMPLE":
        out.append("SAMPLE DATA: synthetic, fictional companies.")
    for key, text in [("rows_dropped_invalid_amount", "rows dropped: amount missing, zero or not a number"),
                      ("rows_dropped_unparseable_year", "rows dropped: financial year not readable"),
                      ("exact_duplicates_removed", "exact duplicate rows removed"),
                      ("rows_missing_state", "rows with no state (excluded from state totals)"),
                      ("rows_multiple_states", "rows listing several states or PAN India (not attributed to one state)"),
                      ("rows_missing_district", "rows with no district"),
                      ("rows_sector_other_or_unmapped", "rows whose sector maps to 'other'")]:
        if report.get(key):
            out.append(f"{report[key]} {text}.")
    return out


class CSRStore(ABC):
    @abstractmethod
    def top_funders(self, sectors: list[str], state: str, district: str, years: int, limit: int = 20) -> tuple[list[Candidate], dict]:
        """Return (candidates, query_info). query_info records the SQL and params for the decision trace."""

    @abstractmethod
    def company_profile(self, company_id: str) -> dict: ...

    @abstractmethod
    def data_quality_report(self) -> dict: ...

    @abstractmethod
    def get_rows(self, source_refs: list[str]) -> list[dict]: ...

    @abstractmethod
    def data_source(self) -> str: ...

    def health_check(self) -> None:
        self.data_source()


def _in_clause(prefix: str, values: list[str]) -> tuple[str, dict]:
    names = [f"{prefix}{i}" for i in range(len(values))]
    return "(" + ", ".join(f":{n}" for n in names) + ")", dict(zip(names, values))


def _trend(values: list[float]) -> str:
    """Compare the later half of the window with the earlier half."""
    if sum(v > 0 for v in values) < 2:
        return "unknown"
    half = len(values) // 2
    early, late = values[:half] or [0.0], values[-half:] or [0.0]
    a, b = sum(early) / len(early), sum(late) / len(late)
    if a == 0:
        return "up"
    ratio = b / a
    return "up" if ratio > 1.2 else "down" if ratio < 0.8 else "flat"


class SQLCSRStore(CSRStore):
    """All ranking logic; the subclass provides _query() on a dataset version and current_dataset()."""

    @abstractmethod
    def _query(self, sql: str, params: dict | None, dataset: dict) -> list[dict]:
        """Run SQL on the given dataset version (raises DatasetUnavailable if it cannot be read)."""

    @abstractmethod
    def current_dataset(self) -> dict:
        """{dataset_id, version, sha256} of the active dataset version."""

    # -- provenance ----------------------------------------------------------------
    @contextmanager
    def provenance(self):
        """Record every query in this block (nested blocks share the outer record)."""
        cur = _PROVENANCE.get()
        if cur is not None:
            yield cur
            return
        prov = Provenance()
        token = _PROVENANCE.set(prov)
        try:
            yield prov
        finally:
            _PROVENANCE.reset(token)

    @contextmanager
    def pinned(self, dataset: dict):
        """Run queries on this dataset version ({dataset_id, version, sha256}) instead of the active one."""
        token = _PINNED.set(dict(dataset))
        try:
            yield
        finally:
            _PINNED.reset(token)

    def current_provenance(self, caveats: list[str] | None = None) -> dict | None:
        prov = _PROVENANCE.get()
        return prov.summary(caveats or []) if prov is not None else None

    def dataset_in_use(self) -> dict:
        """The dataset version queries read right now: pinned, else the run's, else the active one."""
        pinned, prov = _PINNED.get(), _PROVENANCE.get()
        if pinned is not None:
            return pinned
        if prov is not None:
            if prov.dataset is None:
                prov.dataset = self.current_dataset()  # the whole run reads one dataset version
            return prov.dataset
        return self.current_dataset()

    def _q(self, template: str, params: dict | None = None) -> list[dict]:
        """Run a query written with @table tokens; record it when provenance is on."""
        tables = sorted(set(_TABLE_RE.findall(template)))
        sql = _TABLE_RE.sub(lambda m: m.group(1), template)
        rows = self._query(sql, params, self.dataset_in_use())
        prov = _PROVENANCE.get()
        if prov is not None:
            prov.queries.append({"template": template, "params": dict(params or {}), "sql": sql, "tables": tables,
                                 "row_count": len(rows), "result_hash": rows_hash(rows)})
        return rows

    def replay_query(self, query: dict, dataset: dict) -> list[dict]:
        """Re-run one recorded query on the recorded dataset version."""
        with self.pinned(dataset):
            return self._q(query["template"], query["params"])

    # -- helpers ---------------------------------------------------------------
    def latest_fy_start(self) -> int:
        rows = self._q("SELECT MAX(fy_start) AS m FROM @gold_funders_sector_state")
        return int(rows[0]["m"])

    def data_source(self) -> str:
        rows = self._q("SELECT value FROM @fb_meta WHERE key = :k", {"k": "data_source"})
        return rows[0]["value"] if rows else "UNKNOWN"

    def data_quality_report(self) -> dict:
        rows = self._q("SELECT value FROM @fb_meta WHERE key = :k", {"k": "data_quality_report"})
        return json.loads(rows[0]["value"]) if rows else {}

    def top_spenders(self, limit: int = 24) -> list[dict]:
        """Largest reported CSR spenders across all years (public filings only)."""
        rows = self._q(
            "SELECT g.company_id, c.company_name, SUM(g.spend_inr) AS spend_inr "
            "FROM @gold_funders_sector_state g JOIN @clean_companies c ON c.company_id = g.company_id "
            "GROUP BY g.company_id, c.company_name ORDER BY spend_inr DESC, g.company_id LIMIT :n",
            {"n": int(limit)},
        )
        return [{"company_name": r["company_name"], "spend_inr": float(r["spend_inr"] or 0)} for r in rows]

    # -- national coverage (precomputed aggregates; never full scans) ---------------------------------------
    def coverage(self, state_lgd: str | None, sectors: list[str], district_lgd: str | None = None) -> dict:
        """GOOD / THIN / NONE for (state, sectors) and, when given, (district, sectors), with the counts behind it."""
        sec_in, sec_params = _in_clause("s", sectors or ["__none__"])
        out = {"sectors": sectors, "state_lgd": state_lgd, "district_lgd": district_lgd}
        if state_lgd:
            p = {"st": state_lgd, **sec_params}
            r = {**self._q(f"SELECT COUNT(DISTINCT company_id) AS n_companies, SUM(spend_inr) AS spend_inr, "
                           f"SUM(n_rows) AS n_rows FROM @agg_state_sector_company WHERE state_lgd = :st AND sector IN {sec_in}", p)[0],
                 **self._q(f"SELECT COUNT(DISTINCT fy) AS n_years FROM @agg_state_sector_fy "
                           f"WHERE state_lgd = :st AND sector IN {sec_in}", p)[0]}
            out["state"] = {**{k: r[k] or 0 for k in r}, "level": coverage_level(r["n_companies"], r["n_years"])}
        if district_lgd:
            r = self._q(f"SELECT SUM(n_companies) AS n_companies, MAX(n_years) AS n_years, SUM(n_rows) AS n_rows, "
                        f"SUM(spend_inr) AS spend_inr FROM @agg_district_sector WHERE district_lgd = :d AND sector IN {sec_in}",
                        {"d": district_lgd, **sec_params})[0]
            out["district"] = {**{k: r[k] or 0 for k in r},
                               "level": coverage_level(r["n_companies"], r["n_years"], district=True)}
        weakest = [v["level"] for k, v in out.items() if k in ("state",) and isinstance(v, dict)]
        out["level"] = weakest[0] if weakest else "NONE"
        out["warning"] = (COVERAGE_WARNING if out["level"] in ("THIN", "NONE") else "")
        return out

    def coverage_states(self, sector: str = "") -> list[dict]:
        """Per state: reported spend, distinct companies, rows, share of rows without a district, coverage level."""
        where, params = ("WHERE sector = :sec", {"sec": sector}) if sector else ("", {})
        cov = {r["state_lgd"]: r for r in self._q(
            f"SELECT state_lgd, SUM(spend_inr) AS spend_inr, SUM(n_rows) AS n_rows, MAX(n_companies) AS n_companies_max, "
            f"MAX(n_years) AS n_years FROM @coverage_state_sector {where} GROUP BY state_lgd", params)}
        comp = {r["state_lgd"]: r for r in self._q(
            f"SELECT state_lgd, COUNT(DISTINCT company_id) AS n_companies FROM @agg_state_sector_company "
            f"{'WHERE sector = :sec' if sector else ''} GROUP BY state_lgd", params)}
        for r in self._q(f"SELECT state_lgd, COUNT(DISTINCT fy) AS n_years FROM @agg_state_sector_fy "
                         f"{'WHERE sector = :sec' if sector else ''} GROUP BY state_lgd", params):
            comp.setdefault(r["state_lgd"], {})["n_years"] = r["n_years"]
        loc = {r["state_lgd"]: r for r in self._q("SELECT * FROM @agg_state_locations")}
        out = {}
        for st in set(cov) | set(loc):
            c, k, lo = cov.get(st, {}), comp.get(st, {}), loc.get(st, {})
            n_rows_all = lo.get("n_rows") or 0
            out[st] = {"state_lgd": st, "spend_inr": c.get("spend_inr") or 0, "n_rows": c.get("n_rows") or 0,
                       "n_companies": k.get("n_companies") or 0, "n_years": k.get("n_years") or 0,
                       "share_no_district": round((lo.get("n_rows_no_district") or 0) / n_rows_all, 3) if n_rows_all else None,
                       "level": coverage_level(k.get("n_companies") or 0, k.get("n_years") or 0)}
        return list(out.values())

    def coverage_districts(self, state_lgd: str, sector: str = "") -> list[dict]:
        where = "AND sector = :sec" if sector else ""
        rows = self._q(f"SELECT district_lgd, district, SUM(spend_inr) AS spend_inr, SUM(n_rows) AS n_rows, "
                       f"MAX(n_years) AS n_years FROM @agg_district_sector WHERE state_lgd = :st {where} "
                       f"GROUP BY district_lgd, district", {"st": state_lgd, **({"sec": sector} if sector else {})})
        comp = {r["district_lgd"]: r["n"] for r in self._q(
            f"SELECT district_lgd, COUNT(DISTINCT company_id) AS n FROM @clean_projects WHERE state_lgd = :st "
            f"AND district_lgd IS NOT NULL {where} GROUP BY district_lgd", {"st": state_lgd, **({"sec": sector} if sector else {})})}
        return [{**r, "n_companies": comp.get(r["district_lgd"], 0),
                 "level": coverage_level(comp.get(r["district_lgd"], 0), r["n_years"] or 0, district=True)} for r in rows]

    def similar_states(self, state_lgd: str, sectors: list[str], k: int = 3) -> list[dict]:
        """States whose reported CSR sector mix is most like this state's (cosine similarity), among states with
        GOOD coverage in these sectors. Similar by data, not by geography: used only as labelled fallback evidence."""
        rows = self._q("SELECT state_lgd, sector, spend_inr FROM @coverage_state_sector")
        vec: dict[str, dict[str, float]] = defaultdict(dict)
        for r in rows:
            vec[r["state_lgd"]][r["sector"]] = float(r["spend_inr"] or 0)
        def norm(v):
            t = sum(v.values()) or 1.0
            return {s: x / t for s, x in v.items()}
        if not vec.get(state_lgd):
            return []  # no filings in this state: there is nothing to compare its sector mix with
        me = norm(vec[state_lgd])
        good = {c["state_lgd"] for c in self.coverage_states() if c["level"] == "GOOD"}
        sec_ok = {r["state_lgd"] for r in rows if r["sector"] in sectors}
        out = []
        for st, v in vec.items():
            if st == state_lgd or st not in good or st not in sec_ok:
                continue
            o = norm(v)
            dot = sum(me.get(s, 0) * o.get(s, 0) for s in set(me) | set(o))
            na = sum(x * x for x in me.values()) ** 0.5
            nb = sum(x * x for x in o.values()) ** 0.5
            out.append({"state_lgd": st, "similarity": round(dot / (na * nb), 3) if na and nb else 0.0})
        return sorted(out, key=lambda x: (-x["similarity"], x["state_lgd"]))[:k]

    def funders_in(self, sectors: list[str], min_fy: int, state_lgds: list[str] | None = None, all_india: bool = False,
                   limit: int = 10) -> list[dict]:
        """Companies by reported spend in these sectors, either in the given states or on all-India rows."""
        sec_in, sec_params = _in_clause("s", sectors)
        params = {"min_fy": min_fy, "n": int(limit), **sec_params}
        if all_india:
            where = "state_quality = 'all_india'"
        else:
            st_in, st_params = _in_clause("t", state_lgds or ["__none__"])
            where = f"state_lgd IN {st_in}"
            params.update(st_params)
        rows = self._q(
            f"SELECT p.company_id, c.company_name, SUM(p.amount_inr) AS spend_inr, COUNT(DISTINCT p.fy) AS n_years, "
            f"COUNT(*) AS n_rows, MIN(p.source_ref) AS sample_ref FROM @clean_projects p JOIN @clean_companies c "
            f"ON c.company_id = p.company_id WHERE {where} AND p.sector IN {sec_in} AND p.fy_start >= :min_fy "
            f"GROUP BY p.company_id, c.company_name ORDER BY spend_inr DESC, p.company_id LIMIT :n", params)
        return [dict(r) for r in rows]

    def unmapped_sector_labels(self, state_lgds: list[str] | None = None, limit: int = 200) -> list[dict]:
        """Raw filing labels that fell into 'other' (the admin's unmapped-sectors queue), most rows first."""
        where, params = "", {"n": int(limit)}
        if state_lgds is not None:
            st_in, st_params = _in_clause("t", state_lgds or ["__none__"])
            where, params = f"AND state_lgd IN {st_in}", {**params, **st_params}
        return self._q(f"SELECT sector_raw, COUNT(*) AS n_rows, COUNT(DISTINCT company_id) AS n_companies "
                       f"FROM @clean_projects WHERE sector = 'other' {where} GROUP BY sector_raw "
                       f"ORDER BY n_rows DESC, sector_raw LIMIT :n", params)

    def get_rows(self, source_refs: list[str]) -> list[dict]:
        if not source_refs:
            return []
        clause, params = _in_clause("r", source_refs[:200])
        return self._q(
            "SELECT row_id, source_ref, source_file, source_line, company_id, company_name, company_name_raw, cin, "
            "sector, sector_raw, state, states_raw, state_quality, district, fy, amount_inr, outlay_inr "
            f"FROM @clean_projects WHERE source_ref IN {clause} ORDER BY fy, amount_inr DESC",
            params,
        )

    def company_profile(self, company_id: str) -> dict:
        comp = self._q("SELECT * FROM @clean_companies WHERE company_id = :c", {"c": company_id})
        if not comp:
            return {}
        info = dict(comp[0])
        info["name_variants"] = json.loads(info.get("name_variants") or "[]")
        info["spend_by_sector_year"] = self._q(
            "SELECT sector, fy, SUM(spend_inr) AS amount_inr, SUM(n_projects) AS n_rows FROM @gold_funders_sector_state "
            "WHERE company_id = :c GROUP BY sector, fy ORDER BY fy, sector",
            {"c": company_id},
        )
        info["spend_by_state"] = self._q(
            "SELECT state_quality, state, states_raw, SUM(amount_inr) AS amount_inr, COUNT(*) AS n_rows "
            "FROM @clean_projects WHERE company_id = :c GROUP BY state_quality, state, states_raw "
            "ORDER BY amount_inr DESC",
            {"c": company_id},
        )
        info["reported_gap"] = self._q(
            "SELECT fy, spend_inr, outlay_inr, reported_gap_inr, n_projects_with_outlay FROM @gold_company_gap "
            "WHERE company_id = :c ORDER BY fy",
            {"c": company_id},
        )
        info["caveats"] = self._caveats(info)
        if not any(g["reported_gap_inr"] is not None for g in info["reported_gap"]):
            info["caveats"].append("Reported gap (outlay minus spend): unknown; these filings report no project outlay.")
        info["source_refs"] = [r["source_ref"] for r in self._q(
            "SELECT source_ref FROM @clean_projects WHERE company_id = :c ORDER BY fy DESC, amount_inr DESC LIMIT 200",
            {"c": company_id})]
        return info

    @staticmethod
    def _caveats(c: dict) -> list[str]:
        out = []
        if int(c.get("n_name_variants") or 0) > 1:
            out.append(f"Company name merged from {c['n_name_variants']} spelling variants in the filings.")
        if int(c.get("rows_missing_state") or 0):
            out.append(f"State missing in {c['rows_missing_state']} of this company's rows (excluded from state totals).")
        if int(c.get("rows_multiple_states") or 0):
            out.append(f"{c['rows_multiple_states']} rows list multiple states or PAN India (not attributed to any one state).")
        if int(c.get("duplicates_removed") or 0):
            out.append(f"{c['duplicates_removed']} exact duplicate rows were removed for this company.")
        if not c.get("cin"):
            out.append("No CIN in the filings; identity is based on the normalized name only.")
        return out

    # -- main query -----------------------------------------------------------
    def top_funders(self, sectors, state, district, years, limit=20):
        latest = self.latest_fy_start()
        min_fy = latest - years + 1
        window = [f"{y}-{str(y + 1)[-2:]}" for y in range(min_fy, latest + 1)]
        sec_in, sec_params = _in_clause("s", sectors)
        params = {"state": state, "district": (district or "").strip().title(), "state_like": f"%{state}%",
                  "min_fy": min_fy, **sec_params}
        is_sec = f"sector IN {sec_in}"
        is_state = "state_quality = 'ok' AND state = :state"
        sql = f"""
SELECT company_id, fy,
  SUM(CASE WHEN {is_sec} AND {is_state} THEN spend_inr ELSE 0 END) AS ss_amt,
  SUM(CASE WHEN {is_sec} AND {is_state} THEN n_projects ELSE 0 END) AS ss_n,
  SUM(CASE WHEN {is_sec} THEN spend_inr ELSE 0 END) AS sec_amt,
  SUM(CASE WHEN {is_sec} THEN n_projects ELSE 0 END) AS sec_n,
  SUM(CASE WHEN {is_state} THEN spend_inr ELSE 0 END) AS st_amt,
  SUM(CASE WHEN {is_state} AND district = :district THEN spend_inr ELSE 0 END) AS dist_amt,
  SUM(CASE WHEN {is_sec} AND ((state_quality = 'multiple' AND states LIKE :state_like) OR state_quality = 'all_india')
      THEN spend_inr ELSE 0 END) AS multi_amt,
  SUM(spend_inr) AS total_amt
FROM @gold_funders_sector_state
WHERE fy_start >= :min_fy
GROUP BY company_id, fy""".strip()
        def _new_agg() -> dict:
            a: dict = defaultdict(float)
            a["years"] = {}
            return a

        agg: dict[str, dict] = defaultdict(_new_agg)
        for r in self._q(sql, params):
            a = agg[r["company_id"]]
            for k in ("ss_amt", "ss_n", "sec_amt", "sec_n", "st_amt", "dist_amt", "multi_amt", "total_amt"):
                a[k] += float(r[k] or 0)
            a["years"][r["fy"]] = float(r["ss_amt"] or 0)

        pool = [cid for cid, a in agg.items() if a["sec_amt"] > 0 or a["st_amt"] > 0]
        pool.sort(key=lambda cid: (-agg[cid]["ss_amt"], -agg[cid]["sec_amt"], -agg[cid]["st_amt"], cid))
        chosen = pool[:limit]
        query_info = {"sql": sql, "params": params, "fy_window": window, "candidates_considered": len(pool),
                      "tables": ["gold_funders_sector_state", "clean_companies", "clean_projects"]}
        if not chosen:
            return [], query_info

        comp_in, comp_params = _in_clause("c", chosen)
        companies = {c["company_id"]: c for c in self._q(
            f"SELECT * FROM @clean_companies WHERE company_id IN {comp_in}", comp_params)}
        ev_sql = (
            f"SELECT company_id, source_ref, fy, amount_inr, sector, sector_raw, state, states_raw, state_quality, "
            f"district, states FROM @clean_projects WHERE company_id IN {comp_in} AND fy_start >= :min_fy AND "
            f"({is_sec} OR ({is_state}))"
        )
        evid: dict[str, list[Evidence]] = defaultdict(list)
        for r in self._q(ev_sql, {**comp_params, **sec_params, "state": state, "min_fy": min_fy}):
            in_sec = r["sector"] in sectors
            in_state = r["state_quality"] == "ok" and r["state"] == state
            if in_sec and in_state:
                kind = "sector+state"
            elif in_sec and (r["state_quality"] == "all_india"
                             or (r["state_quality"] == "multiple" and state in (r["states"] or ""))):
                kind = "multi_state"
            elif in_sec:
                kind = "sector"
            else:
                kind = "state"
            evid[r["company_id"]].append(Evidence(
                source_ref=r["source_ref"], fy=r["fy"], amount_inr=float(r["amount_inr"]), sector=r["sector"],
                sector_raw=r["sector_raw"], state=r["state"], states_raw=r["states_raw"] or "",
                state_quality=r["state_quality"], district=r["district"], kind=kind,
            ))
        priority = {"sector+state": 0, "multi_state": 1, "sector": 2, "state": 3}

        out = []
        for cid in chosen:
            a = agg[cid]
            c = companies.get(cid, {})
            per_year = {fy: a["years"].get(fy, 0.0) for fy in window}
            ev = sorted(evid[cid], key=lambda e: (priority[e.kind], -e.amount_inr, e.source_ref))[:EVIDENCE_PER_COMPANY]
            out.append(Candidate(
                company_id=cid,
                company_name=c.get("company_name", cid),
                cin=c.get("cin") or "",
                sector_state_spend=a["ss_amt"],
                sector_spend_any_state=a["sec_amt"],
                state_spend_any_sector=a["st_amt"],
                total_spend=a["total_amt"],
                district_spend=a["dist_amt"],
                multi_state_sector_spend=a["multi_amt"],
                years_funded=sum(v > 0 for v in per_year.values()),
                years_window=len(window),
                fys_funded=[fy for fy, v in per_year.items() if v > 0],
                avg_ticket_sector=(a["sec_amt"] / a["sec_n"]) if a["sec_n"] else None,
                n_rows_sector_state=int(a["ss_n"]),
                trend=_trend(list(per_year.values())),
                per_year_sector_state=per_year,
                evidence=ev,
                caveats=self._caveats(c) if c else [],
            ))
        return out, query_info
