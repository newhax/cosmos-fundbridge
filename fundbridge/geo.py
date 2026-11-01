"""Geography backbone: Local Government Directory (LGD) states/UTs and districts, aliases, and a review queue.

Source files (data/raw/geo/) are loaded into data/processed/geo/geo.sqlite as a new geo_version each time.
Nothing about the number of states or districts is hardcoded: everything is read from the files.

Matching is deliberately strict. A place resolves only by
  1. its LGD code (when a dataset carries one and it agrees with the name), or
  2. an exact normalised name of the right level (districts only within their own state), or
  3. an alias (curated file fundbridge/geo_aliases.csv, or added by an admin).
There is no fuzzy matching: anything else is recorded in the unmapped-locations queue for an admin.

Special buckets (never reassigned to a state): ALL_INDIA, MULTIPLE, NOT_SPECIFIED.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import PROCESSED_DIR, RAW_DIR, ROOT

GEO_RAW_DIR = RAW_DIR / "geo"
GEO_DB = PROCESSED_DIR / "geo" / "geo.sqlite"
SOURCES_FILE = GEO_RAW_DIR / "SOURCES.json"  # where each raw file came from (written by scripts/geo.py fetch)
CURATED_ALIASES = ROOT / "fundbridge" / "geo_aliases.csv"
UT_REFERENCE = ROOT / "fundbridge" / "geo_union_territories.json"

ALL_INDIA, MULTIPLE, NOT_SPECIFIED = "ALL_INDIA", "MULTIPLE", "NOT_SPECIFIED"
BUCKETS = (ALL_INDIA, MULTIPLE, NOT_SPECIFIED)
_ALL_INDIA_WORDS = {"all india", "pan india", "pan-india", "india", "nationwide", "national", "across india"}
_NOT_SPECIFIED_WORDS = {"", "not mentioned", "not specified", "na", "n/a", "nil", "none", "unknown", "-"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS geo_version (
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, files TEXT NOT NULL, retrieved_at TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS states_uts (
    geo_version INTEGER NOT NULL, lgd_code TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('state', 'ut', 'unknown')),
    status TEXT NOT NULL CHECK (status IN ('active', 'merged', 'legacy')), merged_into TEXT, census_2011_code TEXT,
    PRIMARY KEY (geo_version, lgd_code));
CREATE TABLE IF NOT EXISTS districts (
    geo_version INTEGER NOT NULL, lgd_code TEXT NOT NULL, state_lgd_code TEXT NOT NULL, name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'merged', 'legacy')), census_2011_code TEXT,
    PRIMARY KEY (geo_version, lgd_code));
CREATE INDEX IF NOT EXISTS ix_districts_state ON districts(geo_version, state_lgd_code);
-- alias_key is the normalised alias; state_lgd_code scopes district aliases ('' for state aliases).
-- canonical_lgd_code may hold several codes separated by '|' for a district that was split.
CREATE TABLE IF NOT EXISTS geo_aliases (
    alias_key TEXT NOT NULL, alias TEXT NOT NULL, canonical_type TEXT NOT NULL CHECK (canonical_type IN ('state', 'district')),
    state_lgd_code TEXT NOT NULL DEFAULT '', canonical_lgd_code TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('variant', 'old_name', 'abbreviation', 'split', 'merged')),
    source TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL,
    PRIMARY KEY (alias_key, canonical_type, state_lgd_code));
CREATE TABLE IF NOT EXISTS geo_unmapped (
    id INTEGER PRIMARY KEY, raw TEXT NOT NULL, raw_key TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('state', 'district')),
    state_lgd_code TEXT NOT NULL DEFAULT '', context TEXT DEFAULT '', source TEXT DEFAULT '', count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'ignored')),
    resolved_code TEXT, resolved_by TEXT, resolved_at TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    UNIQUE (raw_key, kind, state_lgd_code));
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def key(name: str) -> str:
    """Normalised comparison key: lower case, '&' -> 'and', punctuation dropped, 'the ' and ' district' removed."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^the ", "", s)
    s = re.sub(r" district$", "", s)
    return s


def code(c) -> str:
    """LGD codes as plain digit strings without leading zeros ('08' -> '8')."""
    c = str(c or "").strip()
    return str(int(c)) if c.isdigit() else c


@dataclass
class GeoMatch:
    code: str | None            # LGD code, a bucket name, or None when unresolved
    level: str                  # state | district | bucket | none
    quality: str                # ok | all_india | multiple | missing | unmapped | conflict | legacy_mapped | split | code_only
    name: str = ""
    note: str = ""


# -- loading -----------------------------------------------------------------------------------------------
def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _find(raw_dir: Path, kind: str) -> Path:
    hits = sorted(p for p in raw_dir.glob("*.csv") if kind in p.name.lower() and "sub" not in p.name.lower())
    if not hits:
        raise FileNotFoundError(f"No {kind} LGD CSV in {raw_dir}. Run: python scripts/geo.py fetch")
    return hits[0]


def load(raw_dir: Path = GEO_RAW_DIR, db_path: Path = GEO_DB, aliases_file: Path = CURATED_ALIASES,
         ut_file: Path = UT_REFERENCE) -> dict:
    """Load the LGD state + district CSVs as a new geo_version. Returns a summary."""
    st_file, di_file = _find(raw_dir, "state"), _find(raw_dir, "district")
    sources = json.loads((raw_dir / "SOURCES.json").read_text(encoding="utf-8")) if (raw_dir / "SOURCES.json").exists() else {}
    states = list(csv.DictReader(st_file.open(encoding="utf-8-sig")))
    districts = list(csv.DictReader(di_file.open(encoding="utf-8-sig")))
    uts = json.loads(ut_file.read_text(encoding="utf-8")) if ut_file.exists() else {"names": []}
    ut_keys = {key(n) for n in uts.get("names", [])}

    with_districts = {code(d["state_code"]) for d in districts}
    by_key = {key(s["state_name"]): code(s["state_code"]) for s in states}
    files = [{"name": p.name, "sha256": _sha256(p), **sources.get(p.name, {})} for p in (st_file, di_file)]
    retrieved = max((f.get("downloaded_at") or "" for f in files), default="") or None
    con = _connect(db_path)
    with _LOCK, con:
        cur = con.execute("INSERT INTO geo_version (source, files, retrieved_at, note, created_at) VALUES (?,?,?,?,?)",
                          (sources.get("_source", "LGD CSV files in data/raw/geo"), json.dumps(files), retrieved,
                           "status derived: a state/UT with no districts in the district file is 'legacy'; "
                           "its successor comes from fundbridge/geo_aliases.csv (relation 'merged')", _now()))
        gv = cur.lastrowid
        for s in states:
            c = code(s["state_code"])
            kind = "ut" if key(s["state_name"]) in ut_keys else ("state" if ut_keys else "unknown")
            con.execute("INSERT INTO states_uts VALUES (?,?,?,?,?,?,?)",
                        (gv, c, s["state_name"].strip(), kind, "active" if c in with_districts else "legacy", None,
                         code(s.get("census_2011_state_code"))))
        for d in districts:
            con.execute("INSERT INTO districts VALUES (?,?,?,?,?,?)",
                        (gv, code(d["district_code"]), code(d["state_code"]), d["district_name"].strip(), "active",
                         code(d.get("census_2011_district_code"))))
        added, skipped = _load_curated_aliases(con, gv, aliases_file, by_key)
        # legacy states point at their successor when the curated file says so
        for a in con.execute("SELECT * FROM geo_aliases WHERE canonical_type = 'state' AND relation = 'merged'"):
            old = by_key.get(a["alias_key"])
            if old:
                con.execute("UPDATE states_uts SET status = 'merged', merged_into = ? WHERE geo_version = ? AND lgd_code = ?",
                            (a["canonical_lgd_code"], gv, old))
    summary = {"geo_version": gv, "files": files, "states_uts": len(states), "districts": len(districts),
               "legacy_or_merged": [dict(r) for r in con.execute(
                   "SELECT lgd_code, name, status, merged_into FROM states_uts WHERE geo_version = ? AND status != 'active'", (gv,))],
               "aliases_added": added, "aliases_skipped": skipped}
    con.close()
    _CACHE.clear()
    return summary


def _load_curated_aliases(con, gv: int, path: Path, state_by_key: dict) -> tuple[int, list[str]]:
    """Curated aliases name their target by NAME (never by a hardcoded code); targets missing from the
    loaded files are skipped and reported."""
    if not path.exists():
        return 0, []
    added, skipped = 0, []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        if r["type"] == "state":
            targets = [state_by_key.get(key(t)) for t in r["canonical"].split("|")]
            scope = ""
        else:
            scope = state_by_key.get(key(r["state"]))
            targets = [x["lgd_code"] for t in r["canonical"].split("|") for x in con.execute(
                "SELECT lgd_code FROM districts WHERE geo_version = ? AND state_lgd_code = ? AND lower(name) = lower(?)",
                (gv, scope or "", t.strip()))] if scope else []
            if len(targets) != len(r["canonical"].split("|")):
                targets = [None]
        if not targets or None in targets:
            skipped.append(f"{r['alias']} -> {r['canonical']}")
            continue
        con.execute("INSERT OR IGNORE INTO geo_aliases VALUES (?,?,?,?,?,?,?,?,?)",
                    (key(r["alias"]), r["alias"], r["type"], scope or "", "|".join(targets), r["relation"],
                     "curated", r.get("note", ""), _now()))
        added += 1
    return added, skipped


# -- lookups -------------------------------------------------------------------------------------------------
_LOCK = threading.RLock()
_CACHE: dict = {}


class Geo:
    """Read access to one geo_version (default: the latest)."""

    def __init__(self, db_path: Path = GEO_DB, version: int | None = None):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found. Run: python scripts/geo.py load")
        self.db_path = Path(db_path)
        self.con = _connect(self.db_path)
        self.version = version or self.con.execute("SELECT MAX(id) FROM geo_version").fetchone()[0]
        if self.version is None:
            raise FileNotFoundError("No geography loaded. Run: python scripts/geo.py load")
        self._states = {r["lgd_code"]: dict(r) for r in self.con.execute(
            "SELECT * FROM states_uts WHERE geo_version = ?", (self.version,))}
        self._districts = {r["lgd_code"]: dict(r) for r in self.con.execute(
            "SELECT * FROM districts WHERE geo_version = ?", (self.version,))}
        self._state_key = {key(s["name"]): c for c, s in self._states.items()}
        self._district_key = {(d["state_lgd_code"], key(d["name"])): c for c, d in self._districts.items()}
        self._district_names = {key(d["name"]) for d in self._districts.values()}

    # reference data
    def states(self, include_inactive: bool = False) -> list[dict]:
        out = [s for s in self._states.values() if include_inactive or s["status"] == "active"]
        return sorted(out, key=lambda s: s["name"])

    def districts(self, state_code: str | None = None) -> list[dict]:
        out = [d for d in self._districts.values() if state_code is None or d["state_lgd_code"] == code(state_code)]
        return sorted(out, key=lambda d: d["name"])

    def state(self, c: str) -> dict | None:
        return self._states.get(code(c))

    def district(self, c: str) -> dict | None:
        return self._districts.get(code(c))

    def info(self) -> dict:
        r = dict(self.con.execute("SELECT * FROM geo_version WHERE id = ?", (self.version,)).fetchone())
        r["files"] = json.loads(r["files"])
        return r

    def _alias(self, k: str, kind: str, scope: str = "") -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM geo_aliases WHERE alias_key = ? AND canonical_type = ? AND state_lgd_code = ?",
                                (k, kind, scope)).fetchone()

    # resolution
    def resolve_state(self, raw: str, lgd_code: str | None = None) -> GeoMatch:
        k = key(raw)
        if k in _NOT_SPECIFIED_WORDS and not lgd_code:
            return GeoMatch(NOT_SPECIFIED, "bucket", "missing")
        if k in _ALL_INDIA_WORDS or code(lgd_code) == "0":
            return GeoMatch(ALL_INDIA, "bucket", "all_india", "All India")
        if code(lgd_code) == "99":
            return GeoMatch(NOT_SPECIFIED, "bucket", "missing")
        parts = [p for p in re.split(r"\s*(?:,|;|\||/)\s*", raw or "") if p.strip()]
        if len(parts) > 1:
            return GeoMatch(MULTIPLE, "bucket", "multiple", note=f"{len(parts)} places listed")
        by_name = self._match_state_name(k)
        if lgd_code and code(lgd_code) in self._states:
            c = code(lgd_code)
            if by_name and by_name.code != c and not self._same_after_merge(by_name.code, c):
                return GeoMatch(None, "none", "conflict", note=f"code {c} is {self._states[c]['name']}, name says {raw!r}")
            return self._follow_merge(c)
        return by_name or GeoMatch(None, "none", "unmapped", note=f"state {raw!r} not recognised")

    def _match_state_name(self, k: str) -> GeoMatch | None:
        if k in self._state_key:
            return self._follow_merge(self._state_key[k])
        a = self._alias(k, "state")
        if a:
            return self._follow_merge(a["canonical_lgd_code"], note=f"{a['relation']}: {a['alias']}")
        return None

    def _follow_merge(self, c: str, note: str = "") -> GeoMatch:
        s = self._states[c]
        if s["status"] == "merged" and s["merged_into"] in self._states:
            t = self._states[s["merged_into"]]
            return GeoMatch(t["lgd_code"], "state", "legacy_mapped", t["name"], f"{s['name']} merged into {t['name']}")
        return GeoMatch(c, "state", "ok", s["name"], note)

    def _same_after_merge(self, a: str, b: str) -> bool:
        return self._follow_merge(a).code == self._follow_merge(b).code

    def resolve_district(self, raw: str, state_code: str, lgd_code: str | None = None) -> GeoMatch:
        """Resolve a district WITHIN a known state. Never searches other states."""
        sc = code(state_code)
        k = key(raw)
        if (k in _NOT_SPECIFIED_WORDS) and not code(lgd_code).strip("0"):
            return GeoMatch(None, "none", "missing")
        by_name = None
        if (sc, k) in self._district_key:
            c = self._district_key[(sc, k)]
            by_name = GeoMatch(c, "district", "ok", self._districts[c]["name"])
        else:
            a = self._alias(k, "district", sc)
            if a and a["relation"] == "split":
                return GeoMatch(None, "district", "split", note=f"{a['alias']} was split into {a['canonical_lgd_code'].count('|') + 1} "
                                                             f"districts; the row is mapped to its state only")
            if a:
                c = a["canonical_lgd_code"]
                by_name = GeoMatch(c, "district", "legacy_mapped" if a["relation"] == "old_name" else "ok",
                                   self._districts[c]["name"], f"{a['relation']}: {a['alias']}")
        lc = code(lgd_code)
        if lc and lc.strip("0") and lc in self._districts:
            d = self._districts[lc]
            if self._follow_merge(d["state_lgd_code"]).code != self._follow_merge(sc).code:
                return GeoMatch(None, "none", "conflict", note=f"district code {lc} ({d['name']}) is not in this state")
            if by_name and by_name.code != lc:
                return GeoMatch(None, "none", "conflict", note=f"code {lc} is {d['name']}, name says {raw!r}")
            if not by_name and k and k != key(d["name"]):
                if k in self._district_names:  # the name is a real district elsewhere: the row contradicts itself
                    return GeoMatch(None, "none", "conflict", note=f"code {lc} is {d['name']}, name says {raw!r}")
                return GeoMatch(lc, "district", "code_only", d["name"], f"name {raw!r} not recognised; official code used")
            return GeoMatch(lc, "district", "ok", d["name"])
        return by_name or GeoMatch(None, "none", "unmapped", note=f"district {raw!r} not recognised in this state")

    # admin queue
    def record_unmapped(self, raw: str, kind: str, state_code: str = "", context: str = "", source: str = "") -> None:
        k = key(raw)
        if not k or k in _NOT_SPECIFIED_WORDS:
            return
        with _LOCK, self.con:
            self.con.execute(
                "INSERT INTO geo_unmapped (raw, raw_key, kind, state_lgd_code, context, source, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(raw_key, kind, state_lgd_code) DO UPDATE SET count = count + 1, "
                "last_seen = excluded.last_seen", (raw.strip()[:200], k, kind, code(state_code), context[:300], source[:100],
                                                  _now(), _now()))

    def unmapped(self, status: str = "open", states: list[str] | None = None) -> list[dict]:
        q, args = "SELECT * FROM geo_unmapped WHERE status = ?", [status]
        if states is not None:
            q += f" AND state_lgd_code IN ({','.join('?' * len(states))})"
            args += [code(s) for s in states]
        return [dict(r) for r in self.con.execute(q + " ORDER BY count DESC, raw", args)]

    def resolve_unmapped(self, item_id: int, target_code: str | None, by: str, relation: str = "variant") -> dict:
        """Admin decision: map an unmapped name to an LGD code (adds an alias) or ignore it (target None)."""
        with _LOCK, self.con:
            it = self.con.execute("SELECT * FROM geo_unmapped WHERE id = ?", (item_id,)).fetchone()
            if not it:
                raise KeyError(item_id)
            if target_code:
                t = code(target_code)
                if it["kind"] == "state" and t not in self._states:
                    raise ValueError("Unknown state LGD code")
                if it["kind"] == "district" and (t not in self._districts or self._districts[t]["state_lgd_code"] != it["state_lgd_code"]):
                    raise ValueError("That district is not in this item's state")
                self.con.execute("INSERT OR REPLACE INTO geo_aliases VALUES (?,?,?,?,?,?,?,?,?)",
                                 (it["raw_key"], it["raw"], it["kind"], it["state_lgd_code"] if it["kind"] == "district" else "",
                                  t, relation, f"admin:{by}", "", _now()))
            self.con.execute("UPDATE geo_unmapped SET status = ?, resolved_code = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
                             ("resolved" if target_code else "ignored", code(target_code) if target_code else None, by, _now(), item_id))
        return dict(self.con.execute("SELECT * FROM geo_unmapped WHERE id = ?", (item_id,)).fetchone())


def get_geo() -> Geo:
    """Shared Geo for the latest version (re-opened when a new version is loaded)."""
    with _LOCK:
        g = _CACHE.get("geo")
        if g is None or not GEO_DB.exists():
            g = _CACHE["geo"] = Geo()
        return g
