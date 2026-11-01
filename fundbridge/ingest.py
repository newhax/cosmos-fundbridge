"""CSV -> cleaned rows + data-quality report + SQLite database.

Every output row keeps a source_ref (file name + CSV line number + financial
year) so any number shown in the UI can be traced back to the original filing.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .normalize import INDIAN_STATES, clean_district, company_key, display_name, normalize_state, parse_amount, parse_fy
from .sectors import map_sector

HEADER_ALIASES = {
    "company": ["company name", "company", "company_name", "name of the company"],
    "cin": ["cin", "cnn", "cin/cnn", "cin no", "corporate identification number"],
    "sector": ["development area", "sector", "csr development area", "development_area", "csr sector", "development_sector"],
    "state": ["state", "state name", "csr state", "state_name"],
    "district": ["district", "district name", "csr district", "district_name"],
    "fy": ["financial year", "fy", "year", "financial_year"],
    "amount": ["amount spent (inr)", "amount", "project amount spent (inr)", "amount spent", "amount_inr", "amount spent (in inr)",
               "amount_spent_in_lakhs", "amount spent (in lakhs)"],
    # Optional; only used so two different projects with identical figures are not dropped as duplicates.
    "project": ["csr_project_name", "project name", "csr project name", "project"],
    # Optional; the project's reported outlay (budget). Used only for the reported gap (outlay - spent).
    "outlay": ["project_outlay_amount_in_lakhs", "project outlay (inr)", "outlay (inr)", "project outlay"],
    # Optional LGD codes (the MCA / India Data Portal export has them): checked against the names, never trusted blindly.
    "state_code": ["state_code", "state lgd code", "lgd state code"],
    "district_code": ["district_code", "district lgd code", "lgd district code"],
}
REQUIRED = ["company", "sector", "state", "fy", "amount"]
# Amount columns reported in lakhs (MCA / India Data Portal export) -> multiply to INR.
AMOUNT_MULTIPLIER = {"amount_spent_in_lakhs": 100_000, "amount spent (in lakhs)": 100_000,
                     "project_outlay_amount_in_lakhs": 100_000}

COLUMNS = [
    "row_id", "source_file", "source_line", "source_ref", "company_id", "company_name", "company_name_raw",
    "name_key", "cin", "sector", "sector_raw", "state", "states", "states_raw", "state_quality", "district", "fy",
    "fy_start", "amount_inr", "outlay_inr", "state_lgd", "district_lgd", "district_quality", "geo_flag",
]


def _map_headers(headers: list[str]) -> dict[str, str]:
    norm = {h.strip().lower(): h for h in headers}
    out = {}
    for field, aliases in HEADER_ALIASES.items():
        for a in aliases:
            if a in norm:
                out[field] = norm[a]
                break
    missing = [f for f in REQUIRED if f not in out]
    if missing:
        raise ValueError(f"CSV is missing required columns {missing}; found {headers}")
    return out


def make_source_ref(file: str, line: int, fy: str) -> str:
    return f"{file}#L{line}|FY{fy}"


def read_csv(path: Path) -> tuple[dict[str, str], list[tuple[int, dict]]]:
    """Return (field -> source header, [(csv line number, raw row)]). Line 1 is the header."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        hmap = _map_headers(reader.fieldnames or [])
        return hmap, [(i + 2, r) for i, r in enumerate(reader)]


def multiplier_for(hmap: dict[str, str], field: str) -> int:
    return AMOUNT_MULTIPLIER.get(hmap[field].strip().lower(), 1) if field in hmap else 1


def signature(get, hmap: dict[str, str]) -> tuple:
    """Exact-duplicate key: every mapped field, whitespace-collapsed and lower-cased."""
    return tuple(" ".join(get(k).split()).lower() for k in HEADER_ALIASES if k in hmap)


def _geo():
    """The loaded LGD geography, or None when it has not been loaded (rows then keep names only)."""
    from .geo import GEO_DB, get_geo

    return get_geo() if GEO_DB.exists() else None


def _place(geo, raw_state: str, state_code: str, raw_district: str, district_code: str, state: str | None,
           quality: str, district: str | None, report: Counter, unmapped: Counter) -> dict:
    """LGD codes for one row. Multi-state rows stay multiple; PAN India becomes all_india; a state or district
    that cannot be matched with confidence is left empty and flagged (never reassigned)."""
    out = {"state": state, "state_quality": quality, "district": district, "state_lgd": None, "district_lgd": None,
           "district_quality": "missing", "geo_flag": ""}
    if quality == "multiple" and raw_state.strip().lower() in ("pan india", "pan-india", "all india"):
        out.update(state_quality="all_india", district=None)
        return out
    if geo is None or quality == "multiple":
        if quality == "ok":
            out["district_quality"] = "ok" if district else "missing"
        return out
    m = geo.resolve_state(raw_state, state_code or None)
    if m.code == "ALL_INDIA":
        out.update(state=None, state_quality="all_india", district=None)
        return out
    if m.level != "state":
        if m.quality in ("unmapped", "conflict"):
            report[f"rows_state_{m.quality}"] += 1
            unmapped[("state", raw_state.strip(), "")] += 1
            out["geo_flag"] = f"state_{m.quality}"
        out.update(state=None, state_quality="missing", district=None)
        return out
    if m.quality == "legacy_mapped":
        report["rows_state_legacy_mapped"] += 1
        out["geo_flag"] = "state_legacy_mapped"
    out.update(state=m.name, state_quality="ok", state_lgd=m.code)
    d = geo.resolve_district(raw_district, m.code, district_code or None)
    out["district_quality"] = d.quality
    if d.code and d.level == "district":
        out.update(district=d.name, district_lgd=d.code)
        if d.quality != "ok":
            out["geo_flag"] = out["geo_flag"] or f"district_{d.quality}"
    else:
        out["district"] = None
        if d.quality in ("unmapped", "conflict", "split"):
            out["geo_flag"] = out["geo_flag"] or f"district_{d.quality}"
            if d.quality != "conflict":
                unmapped[("district", raw_district.strip(), m.code)] += 1
    report[f"rows_district_{d.quality}"] += 1
    return out


def clean_rows(path: Path) -> tuple[list[dict], dict]:
    """Read one CSV and return (clean_rows, report)."""
    geo = _geo()
    unmapped: Counter = Counter()
    from .taxonomy import label_key, overrides

    label_map = {k: v["sector"] for k, v in overrides()["labels"].items()}  # admin mappings for raw sector labels
    hmap, raw_rows = read_csv(path)
    multiplier = multiplier_for(hmap, "amount")
    outlay_multiplier = multiplier_for(hmap, "outlay")
    report = Counter()
    report["rows_read"] = len(raw_rows)
    seen: set[tuple] = set()
    dup_by_key: Counter = Counter()
    staged = []
    for line, r in raw_rows:
        get = lambda k: (r.get(hmap[k]) or "").strip() if k in hmap else ""  # noqa: E731
        sig = signature(get, hmap)
        if sig in seen:
            report["exact_duplicates_removed"] += 1
            dup_by_key[company_key(get("company"))] += 1
            continue
        seen.add(sig)

        fy = parse_fy(get("fy"))
        amount = parse_amount(get("amount"))
        if amount is not None:
            amount = round(amount * multiplier, 2)
        outlay = parse_amount(get("outlay"))
        if outlay is not None:
            outlay = round(outlay * outlay_multiplier, 2)
        key = company_key(get("company"))
        if not key:
            report["rows_dropped_missing_company"] += 1
            continue
        if fy is None:
            report["rows_dropped_unparseable_year"] += 1
            continue
        if amount is None:
            report["rows_dropped_invalid_amount"] += 1
            continue
        state, states, quality = normalize_state(get("state"))
        if quality == "missing" and get("state_code").strip("0") and get("state_code") != "99":
            quality = "ok"  # the name is unusual but the row carries an LGD code: let the resolver check it
        place = _place(geo, get("state"), get("state_code"), get("district"), get("district_code"), state, quality,
                       clean_district(get("district")), report, unmapped)
        state, quality = place["state"], place["state_quality"]
        if quality == "missing":
            report["rows_missing_state"] += 1
        elif quality in ("multiple", "all_india"):
            report["rows_multiple_states"] += 1
            report[f"rows_{quality}"] += 1
        elif get("state") != state:
            report["state_spellings_normalized"] += 1
        sector = label_map.get(label_key(get("sector"))) or map_sector(get("sector"))
        if sector == "other":
            report["rows_sector_other_or_unmapped"] += 1
        district = place["district"]
        if district is None:
            report["rows_missing_district"] += 1
        cin = get("cin").upper()
        if not cin:
            report["rows_missing_cin"] += 1
        staged.append({
            "source_file": path.name, "source_line": line, "key": key, "company_name_raw": get("company"),
            "cin": cin, "sector": sector, "sector_raw": get("sector"), "state": state, "states": "|".join(states),
            "states_raw": get("state"), "state_quality": quality, "district": district if quality == "ok" else None,
            "fy": fy[0], "fy_start": fy[1], "amount_inr": amount, "outlay_inr": outlay,
            "state_lgd": place["state_lgd"], "district_lgd": place["district_lgd"],
            "district_quality": place["district_quality"], "geo_flag": place["geo_flag"],
        })
    if geo is not None:  # names that could not be matched go to the admin review queue (once per name)
        for (kind, raw, sc), n in unmapped.items():
            geo.record_unmapped(raw, kind, sc, context=f"{n} rows in {path.name}", source="csr dataset")
        report["geo_version"] = geo.version

    # Company identity: CIN where any row of the same name-key has one, else the key.
    key_to_cin: dict[str, Counter] = defaultdict(Counter)
    for s in staged:
        if s["cin"]:
            key_to_cin[s["key"]][s["cin"]] += 1
    variants: dict[str, Counter] = defaultdict(Counter)
    rows = []
    for idx, s in enumerate(staged, start=1):
        cin = s["cin"] or (key_to_cin[s["key"]].most_common(1)[0][0] if key_to_cin[s["key"]] else "")
        company_id = cin or f"NAME:{s['key']}"
        variants[company_id][s["company_name_raw"]] += 1
        rows.append({
            "row_id": idx, "source_file": s["source_file"], "source_line": s["source_line"],
            "source_ref": make_source_ref(s["source_file"], s["source_line"], s["fy"]),
            "company_id": company_id, "company_name": display_name(s["key"]),
            "company_name_raw": s["company_name_raw"], "cin": cin, "sector": s["sector"], "sector_raw": s["sector_raw"],
            "state": s["state"], "states": s["states"], "states_raw": s["states_raw"], "state_quality": s["state_quality"],
            "district": s["district"], "fy": s["fy"], "fy_start": s["fy_start"], "amount_inr": s["amount_inr"],
            "outlay_inr": s["outlay_inr"], "name_key": s["key"], "_key": s["key"],
            "state_lgd": s["state_lgd"], "district_lgd": s["district_lgd"], "district_quality": s["district_quality"],
            "geo_flag": s["geo_flag"],
        })

    merged = {cid: v for cid, v in variants.items() if len(v) > 1}
    sector_map = Counter((r["sector_raw"], r["sector"]) for r in rows)
    out_report = {
        **{k: int(v) for k, v in report.items()},
        "rows_loaded": len(rows),
        "companies": len(variants),
        "companies_with_name_variants": len(merged),
        "name_variants_merged": sum(len(v) - 1 for v in merged.values()),
        "name_merge_examples": [
            {"company_id": cid, "variants": sorted(v)} for cid, v in sorted(merged.items(), key=lambda kv: -len(kv[1]))[:5]
        ],
        "sector_mapping": [
            {"raw": raw, "sector": sec, "rows": n} for (raw, sec), n in sorted(sector_map.items(), key=lambda kv: -kv[1])
        ],
        "_variants": {cid: dict(v) for cid, v in variants.items()},
        "_dups_by_key": dict(dup_by_key),
    }
    return rows, out_report


TABLES = ["clean_projects", "clean_companies", "clean_company_year", "gold_funders_sector_state",
          "gold_company_gap", "agg_state_sector_fy", "agg_district_sector", "coverage_state_sector",
          "agg_state_locations", "agg_state_sector_company", "fb_meta"]

# Aggregates built from clean_projects, in order (plain SQL so they run on SQLite).
DERIVED_SQL = {
    # Spend, outlay and data-quality counts per company and year.
    "clean_company_year": """
SELECT company_id, fy, fy_start,
    SUM(amount_inr) AS spend_inr,
    SUM(outlay_inr) AS outlay_inr,
    SUM(CASE WHEN outlay_inr IS NOT NULL THEN amount_inr ELSE 0.0 END) AS spend_with_outlay_inr,
    COUNT(*) AS n_projects,
    COUNT(outlay_inr) AS n_projects_with_outlay,
    COUNT(DISTINCT sector) AS n_sectors,
    SUM(CASE WHEN state_quality = 'missing' THEN 1 ELSE 0 END) AS n_rows_missing_state,
    SUM(CASE WHEN state_quality IN ('multiple', 'all_india') THEN 1 ELSE 0 END) AS n_rows_multiple_states
FROM clean_projects
GROUP BY company_id, fy, fy_start""",
    # Spend by company x year x sector x state: what the Prospector ranks on.
    "gold_funders_sector_state": """
SELECT company_id, fy, fy_start, sector, state, state_quality, states, district,
    SUM(amount_inr) AS spend_inr,
    COUNT(*) AS n_projects
FROM clean_projects
GROUP BY company_id, fy, fy_start, sector, state, state_quality, states, district""",
    # Reported gap: outlay minus spend on projects that report an outlay; NULL ("unknown") when none do.
    "gold_company_gap": """
SELECT company_id, fy, fy_start, spend_inr, outlay_inr, spend_with_outlay_inr, n_projects, n_projects_with_outlay,
    CASE WHEN n_projects_with_outlay > 0 THEN outlay_inr - spend_with_outlay_inr END AS reported_gap_inr
FROM clean_company_year""",
    # National coverage, precomputed per dataset version (single-state rows with an LGD code only).
    "agg_state_sector_fy": """
SELECT state_lgd, state, sector, fy, fy_start, SUM(amount_inr) AS spend_inr,
    COUNT(DISTINCT company_id) AS n_companies, COUNT(*) AS n_rows
FROM clean_projects WHERE state_quality = 'ok' AND state_lgd IS NOT NULL
GROUP BY state_lgd, state, sector, fy, fy_start""",
    "agg_district_sector": """
SELECT state_lgd, district_lgd, district, sector, SUM(amount_inr) AS spend_inr,
    COUNT(DISTINCT company_id) AS n_companies, COUNT(DISTINCT fy) AS n_years, COUNT(*) AS n_rows
FROM clean_projects WHERE district_lgd IS NOT NULL
GROUP BY state_lgd, district_lgd, district, sector""",
    "coverage_state_sector": """
SELECT state_lgd, sector, SUM(amount_inr) AS spend_inr, COUNT(DISTINCT company_id) AS n_companies,
    COUNT(DISTINCT fy) AS n_years, COUNT(*) AS n_rows
FROM clean_projects WHERE state_quality = 'ok' AND state_lgd IS NOT NULL
GROUP BY state_lgd, sector""",
    # Which companies filed in each (state, sector): distinct-company counts across several sectors without a full scan.
    "agg_state_sector_company": """
SELECT state_lgd, sector, company_id, SUM(amount_inr) AS spend_inr, COUNT(*) AS n_rows
FROM clean_projects WHERE state_quality = 'ok' AND state_lgd IS NOT NULL
GROUP BY state_lgd, sector, company_id""",
    # Per state: how many single-state rows lack a district (share of missing locations).
    "agg_state_locations": """
SELECT state_lgd, COUNT(*) AS n_rows, SUM(CASE WHEN district_lgd IS NULL THEN 1 ELSE 0 END) AS n_rows_no_district,
    COUNT(DISTINCT company_id) AS n_companies, SUM(amount_inr) AS spend_inr
FROM clean_projects WHERE state_quality = 'ok' AND state_lgd IS NOT NULL
GROUP BY state_lgd""",
}

# Data checks run on every built database before it can be activated. Each query returns the offending rows.
_UNIQUE = [
    ("clean_projects", ["row_id"]),
    ("clean_projects", ["source_ref"]),
    ("clean_companies", ["company_id"]),
    ("clean_company_year", ["company_id", "fy"]),
    ("gold_funders_sector_state", ["company_id", "fy", "sector", "state", "state_quality", "states", "district"]),
    ("gold_company_gap", ["company_id", "fy"]),
    ("agg_state_sector_fy", ["state_lgd", "sector", "fy"]),
    ("agg_district_sector", ["district_lgd", "sector"]),
    ("coverage_state_sector", ["state_lgd", "sector"]),
    ("agg_state_locations", ["state_lgd"]),
    ("agg_state_sector_company", ["state_lgd", "sector", "company_id"]),
]
_ROW_CHECKS = {
    "clean_projects: company_id, sector or fy missing":
        "SELECT row_id FROM clean_projects WHERE company_id IS NULL OR company_id = '' OR sector IS NULL OR fy IS NULL",
    "clean_projects: blank CIN on a CIN-identified company":
        "SELECT row_id FROM clean_projects WHERE company_id NOT LIKE 'NAME:%' AND (cin IS NULL OR TRIM(cin) = '')",
    "clean_projects: state_quality not ok/multiple/missing":
        "SELECT row_id FROM clean_projects WHERE state_quality IS NULL "
        "OR state_quality NOT IN ('ok', 'multiple', 'missing', 'all_india')",
    "clean_projects: single-state row without a valid state":
        "SELECT row_id FROM clean_projects WHERE state_quality = 'ok' AND (state IS NULL OR "
        "(state_lgd IS NULL AND state NOT IN ({states})))",
    "clean_projects: amount missing or negative":
        "SELECT row_id FROM clean_projects WHERE amount_inr IS NULL OR amount_inr < 0 OR outlay_inr < 0",
    "clean_companies: rows_total missing or negative":
        "SELECT company_id FROM clean_companies WHERE rows_total IS NULL OR rows_total < 0",
    "clean_company_year: spend missing or negative":
        "SELECT company_id FROM clean_company_year WHERE spend_inr IS NULL OR spend_inr < 0",
    "gold_funders_sector_state: spend or project count missing or negative":
        "SELECT company_id FROM gold_funders_sector_state WHERE spend_inr IS NULL OR spend_inr < 0 "
        "OR n_projects IS NULL OR n_projects < 0",
    "gold_company_gap: spend missing or negative, or negative outlay":
        "SELECT company_id FROM gold_company_gap WHERE spend_inr IS NULL OR spend_inr < 0 OR outlay_inr < 0",
}


def validate_database(con: sqlite3.Connection) -> list[dict]:
    """Run the data checks; returns one entry per failing check (empty list = all passed)."""
    states = ", ".join("'" + s.replace("'", "''") + "'" for s in INDIAN_STATES)
    checks = {f"{t}: duplicate {', '.join(cols)}":
              f"SELECT {', '.join(cols)} FROM {t} GROUP BY {', '.join(cols)} HAVING COUNT(*) > 1"
              for t, cols in _UNIQUE}
    checks.update({name: sql.format(states=states) for name, sql in _ROW_CHECKS.items()})
    failures = []
    for name, sql in checks.items():
        bad = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        if bad:
            failures.append({"check": name, "failing_rows": bad})
    return failures


def build_database(rows: list[dict], report: dict, db_path: Path, data_source: str, source_files: list[str]) -> dict:
    """Write clean rows, per-company caveats, the aggregates and the report into SQLite."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE clean_projects (
            row_id INTEGER PRIMARY KEY, source_file TEXT, source_line INTEGER, source_ref TEXT,
            company_id TEXT, company_name TEXT, company_name_raw TEXT, name_key TEXT, cin TEXT,
            sector TEXT, sector_raw TEXT, state TEXT, states TEXT, states_raw TEXT, state_quality TEXT,
            district TEXT, fy TEXT, fy_start INTEGER, amount_inr REAL, outlay_inr REAL,
            state_lgd TEXT, district_lgd TEXT, district_quality TEXT, geo_flag TEXT
        );
        CREATE INDEX ix_rows_company ON clean_projects(company_id);
        CREATE INDEX ix_rows_cin ON clean_projects(cin);
        CREATE INDEX ix_rows_geo ON clean_projects(state_lgd, district_lgd, sector, fy_start);
        CREATE INDEX ix_rows_sector_state ON clean_projects(sector, state);
        CREATE INDEX ix_rows_source_ref ON clean_projects(source_ref);
        CREATE TABLE clean_companies (
            company_id TEXT PRIMARY KEY, company_name TEXT, cin TEXT, n_name_variants INTEGER,
            name_variants TEXT, rows_total INTEGER, rows_missing_state INTEGER,
            rows_multiple_states INTEGER, duplicates_removed INTEGER
        );
        CREATE TABLE fb_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.executemany(
        f"INSERT INTO clean_projects ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
        [tuple(r[c] for c in COLUMNS) for r in rows],
    )
    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_company[r["company_id"]].append(r)
    dups = report.get("_dups_by_key", {})
    variants = report.get("_variants", {})
    for cid, rs in by_company.items():
        keys = {r["name_key"] for r in rs}
        con.execute(
            "INSERT INTO clean_companies VALUES (?,?,?,?,?,?,?,?,?)",
            (
                cid, rs[0]["company_name"], rs[0]["cin"], len(variants.get(cid, {})),
                json.dumps(sorted(variants.get(cid, {}))), len(rs),
                sum(r["state_quality"] == "missing" for r in rs),
                sum(r["state_quality"] in ("multiple", "all_india") for r in rs),
                sum(dups.get(k, 0) for k in keys),
            ),
        )
    for name, sql in DERIVED_SQL.items():
        con.execute(f"CREATE TABLE {name} AS {sql.strip()}")
    con.execute("CREATE INDEX ix_gold_company ON gold_funders_sector_state(company_id)")
    con.execute("CREATE INDEX ix_gold_state_sector ON gold_funders_sector_state(state, sector, fy_start)")
    con.execute("CREATE INDEX ix_agg_state ON agg_state_sector_fy(state_lgd, sector)")
    con.execute("CREATE INDEX ix_agg_state_company ON agg_state_sector_company(state_lgd, sector)")
    con.execute("CREATE INDEX ix_agg_district ON agg_district_sector(state_lgd, district_lgd, sector)")
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public.update({
        "companies": len(by_company),
        "data_source": data_source,
        "source_files": source_files,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fy_range": [min(r["fy"] for r in rows), max(r["fy"] for r in rows)] if rows else [],
    })
    con.execute("INSERT INTO fb_meta VALUES ('data_quality_report', ?)", (json.dumps(public),))
    con.execute("INSERT INTO fb_meta VALUES ('data_source', ?)", (data_source,))
    con.commit()
    con.close()
    return public


def merge_reports(reports: list[dict]) -> dict:
    """Combine per-file reports (counts add up; examples concatenate)."""
    if len(reports) == 1:
        return reports[0]
    out: dict = {}
    for rep in reports:
        for k, v in rep.items():
            if isinstance(v, int):
                out[k] = out.get(k, 0) + v
            elif isinstance(v, list):
                out.setdefault(k, []).extend(v)
            elif isinstance(v, dict):
                out.setdefault(k, {}).update(v)
    return out
