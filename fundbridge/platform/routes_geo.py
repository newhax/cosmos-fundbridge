"""Geography, national coverage, state CSR portals and the scoped admin's queues.

Public (no login): LGD states/districts, the coverage tables, configured state portal links.
Admin (role admin, scoped by state and language): unmapped locations, unmapped sector labels, the sector
map with Schedule VII references, state portal links, and feedback.
"""
from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import taxonomy
from ..config import ROOT
from ..geo import code
from ..state import STATE
from .auth import require_role

router = APIRouter(prefix="/api")
ADMIN = require_role("admin")
PORTALS_FILE = ROOT / "config" / "state_portals.json"
ABSENT_NOTE = "From reported filings; absent data does not mean no CSR activity."


def P():
    return STATE["platform"]


def csr():
    return STATE["backends"].csr


OLD_VERSION = ("This dataset version was built before coverage tables existed. "
               "Build a new version with: python scripts/ingest.py")


def _coverage_call(fn, *args):
    import sqlite3

    try:
        return fn(*args)
    except sqlite3.OperationalError as e:
        raise HTTPException(409, OLD_VERSION) from e


# -- geography (public) -----------------------------------------------------------------------------------
@router.get("/geo/states")
def states():
    g = P().geo
    return {"geo_version": g.version, "states": [{k: s[k] for k in ("lgd_code", "name", "kind")} for s in g.states()]}


@router.get("/geo/states/{state_lgd}/districts")
def districts(state_lgd: str):
    g = P().geo
    if not g.state(state_lgd):
        raise HTTPException(404, "Unknown state code.")
    return [{"lgd_code": d["lgd_code"], "name": d["name"]} for d in g.districts(state_lgd)]


@router.get("/geo/info")
def geo_info():
    return P().geo.info()


# -- coverage (public) ------------------------------------------------------------------------------------
@router.get("/coverage")
def coverage(sector: str = ""):
    """Every active state/UT from the loaded LGD file, with what the filings report for it (NONE when nothing)."""
    g = P().geo
    have = {c["state_lgd"]: c for c in _coverage_call(csr().coverage_states, sector)}
    rows = []
    for s in g.states():
        c = have.get(s["lgd_code"], {})
        rows.append({"state_lgd": s["lgd_code"], "name": s["name"], "kind": s["kind"],
                     "spend_inr": c.get("spend_inr", 0), "n_companies": c.get("n_companies", 0), "n_rows": c.get("n_rows", 0),
                     "n_years": c.get("n_years", 0), "share_no_district": c.get("share_no_district"),
                     "level": c.get("level", "NONE"), "n_districts": len(g.districts(s["lgd_code"]))})
    rep = csr().data_quality_report()
    return {"note": ABSENT_NOTE, "sector": sector or "all sectors", "geo_version": g.version,
            "dataset": STATE["backends"].status()["dataset"], "states": rows,
            "national": {"rows_all_india": rep.get("rows_all_india", 0), "rows_missing_state": rep.get("rows_missing_state", 0),
                         "rows_read": rep.get("rows_read", 0)}}


@router.get("/coverage/{state_lgd}")
def coverage_state(state_lgd: str, sector: str = ""):
    g = P().geo
    s = g.state(state_lgd)
    if not s:
        raise HTTPException(404, "Unknown state code.")
    have = {d["district_lgd"]: d for d in _coverage_call(csr().coverage_districts, s["lgd_code"], sector)}
    rows = [{"district_lgd": d["lgd_code"], "name": d["name"], "spend_inr": have.get(d["lgd_code"], {}).get("spend_inr", 0),
             "n_companies": have.get(d["lgd_code"], {}).get("n_companies", 0), "n_rows": have.get(d["lgd_code"], {}).get("n_rows", 0),
             "level": have.get(d["lgd_code"], {}).get("level", "NONE")} for d in g.districts(s["lgd_code"])]
    return {"note": ABSENT_NOTE, "state": s["name"], "state_lgd": s["lgd_code"], "sector": sector or "all sectors",
            "districts": rows, "districts_with_no_reported_projects": [r["name"] for r in rows if not r["n_rows"]],
            "portal": portal_for(s["lgd_code"])}


# -- state portals -----------------------------------------------------------------------------------------
def _portals() -> dict:
    data = json.loads(PORTALS_FILE.read_text(encoding="utf-8")) if PORTALS_FILE.exists() else {"portals": {}}
    for s in P().geo.states():  # every state/UT appears, empty unless configured
        data["portals"].setdefault(s["lgd_code"], {"name": s["name"], "url": None, "note": ""})
    return data


def portal_for(state_lgd: str) -> dict | None:
    p = _portals()["portals"].get(code(state_lgd))
    return {"url": p["url"], "note": p.get("note", "")} if p and p.get("url") else None


@router.get("/state-portals")
def state_portals():
    """Only configured links are returned (never a guessed URL)."""
    return {k: v for k, v in _portals()["portals"].items() if v.get("url")}


class PortalReq(BaseModel):
    url: str | None = None
    note: str = ""


@router.put("/admin/state-portals/{state_lgd}")
def set_portal(state_lgd: str, req: PortalReq, user: dict = Depends(ADMIN)):
    P().admin.require_state(user, state_lgd)
    if req.url and not req.url.startswith("https://"):
        raise HTTPException(422, "Portal links must start with https://")
    if req.url and len(req.note.strip()) < 10:
        raise HTTPException(422, "Say where you confirmed this link (at least 10 characters).")
    data = _portals()
    entry = data["portals"].get(code(state_lgd))
    if not entry:
        raise HTTPException(404, "Unknown state code.")
    entry.update(url=req.url or None, note=req.note.strip()[:300] if req.url else "")
    PORTALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PORTALS_FILE.with_name(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PORTALS_FILE)
    P().admin.log(user, "portal.set", f"{state_lgd} -> {req.url}")
    return entry


# -- admin -------------------------------------------------------------------------------------------------
@router.get("/admin/scope")
def admin_scope(user: dict = Depends(ADMIN)):
    return P().admin.scope(user)


@router.get("/admin/geo/unmapped")
def unmapped_locations(status: str = "open", state: str = "", user: dict = Depends(ADMIN)):
    return P().admin.unmapped_locations(user, status, state)


class ResolveReq(BaseModel):
    target_code: str | None = None  # None = ignore


@router.post("/admin/geo/unmapped/{item_id}")
def resolve_location(item_id: int, req: ResolveReq, user: dict = Depends(ADMIN)):
    out = P().admin.resolve_location(user, item_id, req.target_code)
    from ..geo import _CACHE

    _CACHE.clear()  # new alias: re-open the shared Geo
    P()._geo = None
    return out


@router.get("/admin/sectors")
def sector_map(user: dict = Depends(ADMIN)):
    sc = P().admin.scope(user)["states"]
    return {"sectors": taxonomy.sector_table(), "reference": taxonomy.reference(),
            "unmapped_labels": csr().unmapped_sector_labels(sc),
            "label_overrides": taxonomy.overrides()["labels"],
            "local_area_spend": "unknown: the loaded dataset has no local-area spend column"}


class LabelReq(BaseModel):
    label: str
    sector: str


@router.post("/admin/sectors/labels")
def map_label(req: LabelReq, user: dict = Depends(ADMIN)):
    try:
        out = taxonomy.set_label(req.label, req.sector, user["email"])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    P().admin.log(user, "sector.label", f"{req.label} -> {req.sector}")
    return {**out, "applies": "from the next dataset version (python scripts/ingest.py)"}


class ItemsReq(BaseModel):
    items: list[str]
    note: str = ""


@router.put("/admin/sectors/{sector}")
def set_items(sector: str, req: ItemsReq, user: dict = Depends(ADMIN)):
    if P().admin.scope(user)["states"] is not None:
        raise HTTPException(403, "Only national admins edit the sector map.")
    try:
        out = taxonomy.set_sector_items(sector, req.items, req.note, user["email"])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    P().admin.log(user, "sector.items", f"{sector} -> {req.items}")
    return out


@router.get("/admin/feedback")
def admin_feedback(status: str = "", state: str = "", language: str = "", user: dict = Depends(ADMIN)):
    return P().admin.feedback(user, status, state, language)


class FeedbackStatusReq(BaseModel):
    status: str


@router.post("/admin/feedback/{fid}")
def feedback_status(fid: str, req: FeedbackStatusReq, user: dict = Depends(ADMIN)):
    P().admin.set_feedback_status(user, fid, req.status)
    return {"ok": True}
