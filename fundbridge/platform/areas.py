"""Multi-region areas for NGOs, companies, projects and calls (one join table, LGD codes).

An area is {level: all_india | state | district, state: LGD code, district: LGD code}. Roles:
  hq         headquarters (NGO, company): one state, optional district
  operating  where the NGO / company works (self-declared): many states, districts or "whole state"
  focus      where a company's CSR (or one of its calls) wants to fund; also a project's location

Matching (area_match) takes the best overlap: same district > same state > pan-India, with weights from
config.GEO_MATCH_WEIGHTS. Missing areas on either side give None ("unknown"), never a guess.
"""
from __future__ import annotations

from fastapi import HTTPException

from ..config import GEO_MATCH_WEIGHTS
from ..geo import ALL_INDIA, Geo, code

LEVELS = ("all_india", "state", "district")
ROLES = ("hq", "operating", "focus")
MAX_AREAS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('ngo', 'company', 'project', 'call')),
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('hq', 'operating', 'focus')),
    level TEXT NOT NULL CHECK (level IN ('all_india', 'state', 'district')),
    state_lgd TEXT NOT NULL DEFAULT '', district_lgd TEXT NOT NULL DEFAULT '',
    geo_version INTEGER,
    PRIMARY KEY (entity_type, entity_id, role, level, state_lgd, district_lgd));
CREATE INDEX IF NOT EXISTS ix_areas_state ON areas(state_lgd, district_lgd);
"""


def clean(geo: Geo, items: list[dict] | None, role: str = "operating") -> list[dict]:
    """Validate areas given as LGD codes. 422 on anything unknown; duplicates and areas covered by a wider
    one (a district inside a whole-state entry, anything next to all-India) are dropped."""
    out: list[dict] = []
    for it in items or []:
        lvl = (it or {}).get("level")
        if lvl == "all_india":
            out.append({"level": "all_india", "state": "", "district": ""})
            continue
        st = code(it.get("state"))
        if lvl not in ("state", "district") or not geo.state(st) or geo.state(st)["status"] != "active":
            raise HTTPException(422, f"Unknown or inactive state code {it.get('state')!r}.")
        if lvl == "district":
            d = geo.district(it.get("district"))
            if not d or d["state_lgd_code"] != st:
                raise HTTPException(422, f"District code {it.get('district')!r} is not in {geo.state(st)['name']}.")
            out.append({"level": "district", "state": st, "district": d["lgd_code"]})
        else:
            out.append({"level": "state", "state": st, "district": ""})
    if role == "hq" and len(out) > 1:
        raise HTTPException(422, "Headquarters is one place.")
    if len(out) > MAX_AREAS:
        raise HTTPException(422, f"At most {MAX_AREAS} areas.")
    if any(a["level"] == "all_india" for a in out):
        return [{"level": "all_india", "state": "", "district": ""}]
    whole = {a["state"] for a in out if a["level"] == "state"}
    seen, final = set(), []
    for a in out:
        k = (a["level"], a["state"], a["district"])
        if k in seen or (a["level"] == "district" and a["state"] in whole):
            continue
        seen.add(k)
        final.append(a)
    return final


def set_areas(db, entity_type: str, entity_id: str, role: str, areas: list[dict], geo_version: int) -> None:
    with db.tx() as con:
        con.execute("DELETE FROM areas WHERE entity_type = ? AND entity_id = ? AND role = ?", (entity_type, entity_id, role))
        con.executemany("INSERT OR IGNORE INTO areas VALUES (?,?,?,?,?,?,?)",
                        [(entity_type, entity_id, role, a["level"], a["state"], a["district"], geo_version) for a in areas])


def get_areas(db, geo: Geo, entity_type: str, entity_id: str, role: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM areas WHERE entity_type = ? AND entity_id = ?", [entity_type, entity_id]
    if role:
        q += " AND role = ?"
        args.append(role)
    return [describe(geo, {"level": r["level"], "state": r["state_lgd"], "district": r["district_lgd"], "role": r["role"]})
            for r in db.all(q + " ORDER BY role, level, state_lgd, district_lgd", args)]


def describe(geo: Geo, a: dict) -> dict:
    """Add display names (and a flag when a code is no longer in the current geography)."""
    out = dict(a)
    if a["level"] == "all_india":
        out["label"] = "All India"
        return out
    s = geo.state(a["state"])
    d = geo.district(a["district"]) if a["level"] == "district" else None
    out["state_name"] = s["name"] if s else "unknown"
    if a["level"] == "district":
        out["district_name"] = d["name"] if d else "unknown"
        out["label"] = f"{out['district_name']}, {out['state_name']}"
    else:
        out["label"] = f"{out['state_name']} (whole state)"
    if not s or (a["level"] == "district" and not d):
        out["flag"] = "not in the current geography version"
    return out


def from_text(geo: Geo, state_text: str, district_text: str = "", context: str = "") -> list[dict]:
    """Map an old free-text state/district to areas. Unresolved names go to the admin queue (never guessed)."""
    if not (state_text or "").strip():
        return []
    s = geo.resolve_state(state_text)
    if s.code == ALL_INDIA:
        return [{"level": "all_india", "state": "", "district": ""}]
    if s.level != "state":
        if s.quality in ("unmapped", "conflict"):
            geo.record_unmapped(state_text, "state", context=context, source="profile")
        return []
    if (district_text or "").strip():
        d = geo.resolve_district(district_text, s.code)
        if d.code and d.level == "district":
            return [{"level": "district", "state": s.code, "district": d.code}]
        if d.quality in ("unmapped", "conflict"):
            geo.record_unmapped(district_text, "district", s.code, context=context, source="profile")
    return [{"level": "state", "state": s.code, "district": ""}]


def area_match(seeker: list[dict], offer: list[dict], weights: dict | None = None) -> dict:
    """Best overlap between two area lists. Returns {score, level, note}; score None when either side is empty."""
    w = weights or GEO_MATCH_WEIGHTS
    if not seeker or not offer:
        return {"score": None, "level": "unknown", "note": "Location: unknown"}
    def states(xs):
        return {a["state"] for a in xs if a["level"] in ("state", "district")}
    sd = {(a["state"], a["district"]) for a in seeker if a["level"] == "district"}
    od = {(a["state"], a["district"]) for a in offer if a["level"] == "district"}
    if sd & od:
        hit = sorted(sd & od)[0]
        return {"score": w["district"], "level": "district", "note": "Same district", "state": hit[0], "district": hit[1]}
    common = states(seeker) & states(offer)
    if common:
        return {"score": w["state"], "level": "state", "note": "Same state", "state": sorted(common)[0]}
    if any(a["level"] == "all_india" for a in seeker + offer):
        return {"score": w["all_india"], "level": "all_india", "note": "Pan-India match"}
    return {"score": 0.0, "level": "none", "note": "No overlap in location"}
