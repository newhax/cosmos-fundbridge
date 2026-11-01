"""Platform facade: profiles, readiness, directory, CIN matching, NGO-side thread follow-ups."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from ..config import Settings
from ..sectors import clean_causes
from ..stores.memory_base import MemoryStore
from . import areas as A
from .admin import AdminService
from .auth import AuthService
from .db import PlatformDB, loads, new_id, now_iso
from .donors import DonorService, validate_donation_url
from .verification import VerificationService
from .pitches import PitchService
from .projects import SCHEMA as PROJECTS_SCHEMA
from .projects import ProjectService

CREDENTIALS = ("crn", "darpan_id", "has_12a", "has_80g")
LISTABLE = ("state", "district", "causes", "summary", "credentials")
NGO_EDITABLE = ("name", "state", "district", "causes", "summary", "crn", "darpan_id", "has_12a", "has_80g",
                "listed_publicly", "listed_fields", "accepts_individual_donations", "donation_url",
                "impact_updates_enabled", "founded_year", "suggested_gift_inr",
                "pan", "is_section8", "section8_cin", "receives_foreign_funds")
COMPANY_EDITABLE = ("name", "cin", "csr_contact_email", "focus_sectors", "focus_states", "min_ticket", "max_ticket", "pan")



def _causes(values) -> list[str]:
    """Standard sectors plus causes typed under "Other" (see sectors.clean_causes); 422 if one is invalid."""
    try:
        return clean_causes(values)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

class Platform:
    def __init__(self, path: Path, memory: MemoryStore, settings: Settings, csr_getter=None):
        self.settings = settings
        self.db = PlatformDB(path)
        self.auth = AuthService(self.db, settings)
        self.pitches = PitchService(self.db, memory, settings)
        self.donors = DonorService(self, memory)

        def _csr():
            from ..state import STATE
            return STATE["backends"].csr

        self.verification = VerificationService(self, csr_getter or _csr)
        with self.db.tx() as con:
            con.executescript(A.SCHEMA + PROJECTS_SCHEMA + AdminService.SCHEMA)
        self.projects = ProjectService(self)
        self.admin = AdminService(self)
        self._geo = None

    @property
    def geo(self):
        """Current LGD geography (fundbridge/geo.py); loaded lazily so tests can swap it."""
        if self._geo is None:
            from ..geo import get_geo
            self._geo = get_geo()
        return self._geo

    def backfill_areas(self) -> int:
        """Profiles saved before areas existed: derive HQ/operating/focus areas from their old text fields.
        Unresolved names go to the admin queue. Returns the number of profiles updated."""
        n = 0
        for r in self.db.all("SELECT ngo_id, state, district FROM ngo_profiles WHERE ngo_id NOT IN "
                             "(SELECT entity_id FROM areas WHERE entity_type = 'ngo')"):
            got = A.from_text(self.geo, r["state"], r["district"], context=f"ngo {r['ngo_id']}")
            if got:
                A.set_areas(self.db, "ngo", r["ngo_id"], "hq", got[:1], self.geo.version)
                A.set_areas(self.db, "ngo", r["ngo_id"], "operating", got, self.geo.version)
                n += 1
        for r in self.db.all("SELECT company_id, focus_states FROM company_profiles WHERE company_id NOT IN "
                             "(SELECT entity_id FROM areas WHERE entity_type = 'company')"):
            got = [a for s in loads(r["focus_states"], []) for a in A.from_text(self.geo, s, context=f"company {r['company_id']}")]
            if got:
                A.set_areas(self.db, "company", r["company_id"], "focus", A.clean(self.geo, got), self.geo.version)
                n += 1
        return n

    # -- profiles -----------------------------------------------------------------------
    def create_ngo(self, user_id: str, name: str, is_demo: bool = False, **fields) -> str:
        ngo_id = fields.pop("ngo_id", None) or new_id("ngo")
        self.db.run("INSERT INTO ngo_profiles (ngo_id, user_id, name, is_demo, updated_at) VALUES (?,?,?,?,?)",
                    (ngo_id, user_id, name.strip() or "Unnamed NGO", int(is_demo), now_iso()))
        if fields:
            self.update_ngo(ngo_id, fields)
        return ngo_id

    def create_company(self, user_id: str, name: str, is_demo: bool = False, **fields) -> str:
        company_id = fields.pop("company_id", None) or new_id("cmp")
        self.db.run("INSERT INTO company_profiles (company_id, user_id, name, is_demo, updated_at) VALUES (?,?,?,?,?)",
                    (company_id, user_id, name.strip() or "Unnamed company", int(is_demo), now_iso()))
        if fields:
            self.update_company(company_id, fields)
        return company_id

    def ngo_profile(self, ngo_id: str) -> dict:
        r = self.db.one("SELECT * FROM ngo_profiles WHERE ngo_id = ?", (ngo_id,))
        if not r:
            raise HTTPException(404, "NGO profile not found.")
        r["causes"] = loads(r["causes"], [])
        r["listed_fields"] = loads(r["listed_fields"], [])
        r["credential_source"] = loads(r["credential_source"], {})
        for k in ("has_12a", "has_80g", "listed_publicly", "is_demo", "accepts_individual_donations",
                  "impact_updates_enabled", "is_section8", "receives_foreign_funds"):
            r[k] = bool(r[k])
        r["hq_area"] = (A.get_areas(self.db, self.geo, "ngo", ngo_id, "hq") or [None])[0]
        r["areas"] = A.get_areas(self.db, self.geo, "ngo", ngo_id, "operating")
        return r

    def company_profile(self, company_id: str) -> dict:
        r = self.db.one("SELECT * FROM company_profiles WHERE company_id = ?", (company_id,))
        if not r:
            raise HTTPException(404, "Company profile not found.")
        r["focus_sectors"] = loads(r["focus_sectors"], [])
        r["focus_states"] = loads(r["focus_states"], [])
        r["is_demo"] = bool(r["is_demo"])
        r["hq_area"] = (A.get_areas(self.db, self.geo, "company", company_id, "hq") or [None])[0]
        r["operating_areas"] = A.get_areas(self.db, self.geo, "company", company_id, "operating")
        r["focus_areas"] = A.get_areas(self.db, self.geo, "company", company_id, "focus")
        return r

    def _save_areas(self, entity_type: str, entity_id: str, fields: dict, mapping: dict) -> dict:
        """Areas given as LGD codes; returns plain-text fields to keep in sync for older code paths."""
        sync = {}
        for field, role in mapping.items():
            if field not in fields:
                continue
            items = [fields[field]] if role == "hq" and isinstance(fields[field], dict) else fields[field] or []
            got = A.clean(self.geo, items, role)
            A.set_areas(self.db, entity_type, entity_id, role, got, self.geo.version)
            named = [A.describe(self.geo, a) for a in got]
            if role == "hq" and entity_type == "ngo":
                sync["state"] = named[0].get("state_name", "") if named else ""
                sync["district"] = named[0].get("district_name", "") if named else ""
            if role == "focus" and entity_type == "company":
                sync["focus_states"] = sorted({a["state_name"] for a in named if a.get("state_name")})
        return sync

    def update_ngo(self, ngo_id: str, fields: dict) -> dict:
        old = self.ngo_profile(ngo_id)  # 404 if unknown
        if ("state" in fields or "district" in fields) and "hq_area" not in fields:  # older callers send text
            got = A.from_text(self.geo, fields.get("state", old["state"]), fields.get("district", old["district"]),
                              context=f"ngo {ngo_id}")
            fields = {**fields, "hq_area": got[0] if got else None}
            if not old["areas"] and "areas" not in fields:
                fields["areas"] = got
        if fields.get("hq_area") is None and "hq_area" in fields:
            fields["hq_area"] = []
        fields = {**fields, **self._save_areas("ngo", ngo_id, fields, {"hq_area": "hq", "areas": "operating"})}
        cur = self.ngo_profile(ngo_id)
        source = dict(cur["credential_source"])
        sets, vals = [], []
        for k, v in fields.items():
            if k not in NGO_EDITABLE:
                continue
            if k == "causes":
                v = json.dumps(_causes(v))
            elif k == "listed_fields":
                v = json.dumps([f for f in (v or []) if f in LISTABLE])
            elif k in ("has_12a", "has_80g", "listed_publicly", "accepts_individual_donations", "impact_updates_enabled",
                       "is_section8", "receives_foreign_funds"):
                v = int(bool(v))
            elif k == "donation_url":
                v = validate_donation_url(v)
            elif k == "founded_year":
                v = int(v) if v not in (None, "") else None
                if v is not None and not (1850 <= v <= datetime.now().year):
                    raise HTTPException(422, "founded_year looks wrong")
            elif k == "suggested_gift_inr":
                v = float(v) if v not in (None, "") else None
                if v is not None and v <= 0:
                    raise HTTPException(422, "suggested_gift_inr must be positive (INR)")
            elif k in ("pan", "section8_cin"):
                v = (v or "").strip().upper()[:30]
            elif isinstance(v, str):
                v = v.strip()[:2000]
            if k in CREDENTIALS and v != (cur[k] if k not in ("has_12a", "has_80g") else int(cur[k])):
                source[k] = "self"
            sets.append(f"{k} = ?")
            vals.append(v)
        if sets:
            sets += ["credential_source = ?", "updated_at = ?"]
            vals += [json.dumps(source), now_iso(), ngo_id]
            self.db.run(f"UPDATE ngo_profiles SET {', '.join(sets)} WHERE ngo_id = ?", vals)
        return self.ngo_profile(ngo_id)

    def update_company(self, company_id: str, fields: dict) -> dict:
        self.company_profile(company_id)  # 404 if unknown
        if "focus_states" in fields and "focus_areas" not in fields:  # older callers send state names
            fields = {**fields, "focus_areas": [a for st in fields["focus_states"] or []
                                                for a in A.from_text(self.geo, st, context=f"company {company_id}")]}
        fields = {**fields, **self._save_areas("company", company_id, fields,
                                               {"hq_area": "hq", "operating_areas": "operating", "focus_areas": "focus"})}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in COMPANY_EDITABLE:
                continue
            if k in ("focus_sectors",):
                v = json.dumps(_causes(v))
            elif k == "focus_states":
                v = json.dumps([str(s).strip() for s in (v or []) if str(s).strip()])
            elif k in ("min_ticket", "max_ticket"):
                v = float(v) if v not in (None, "") else None
            elif isinstance(v, str):
                v = v.strip()[:500]
            if k == "pan":
                v = v.upper()
            if k == "cin":
                v = v.upper()
            sets.append(f"{k} = ?")
            vals.append(v)
        if sets:
            vals += [now_iso(), company_id]
            self.db.run(f"UPDATE company_profiles SET {', '.join(sets)}, updated_at = ? WHERE company_id = ?", vals)
        return self.company_profile(company_id)

    # -- NGO readiness + directory -------------------------------------------------------------
    @staticmethod
    def credential_label(ngo: dict, field: str) -> str:
        return "self-declared"  # nobody checks credentials by hand

    def readiness(self, ngo: dict) -> list[dict]:
        items = [("crn", "CSR-1 registration number", ngo["crn"]), ("darpan_id", "NGO Darpan ID", ngo["darpan_id"]),
                 ("has_12a", "12A registration", ngo["has_12a"]), ("has_80g", "80G registration", ngo["has_80g"])]
        return [{"key": k, "label": label, "done": bool(v), "value": v if isinstance(v, str) else None,
                 "source": self.credential_label(ngo, k) if v else None} for k, label, v in items]

    def public_listing(self, ngo: dict) -> dict:
        """Only the fields the NGO opted to list (plus its name and the DEMO flag)."""
        st = self.verification.status("ngo", ngo["ngo_id"])
        out = {"ngo_id": ngo["ngo_id"], "name": ngo["name"], "is_demo": ngo["is_demo"],
               "verification": {"level": st["level"], "badge": st["badge"], "warnings": st["warnings"]}}
        for f in ngo["listed_fields"]:
            if f == "credentials":
                out["credentials"] = [{"label": i["label"], "value": i["value"], "source": i["source"]}
                                      for i in self.readiness(ngo) if i["done"]]
            elif f in ngo:
                out[f] = ngo[f]
        return out

    def directory(self, sector: str = "", state: str = "", district: str = "", company_id: str | None = None) -> list[dict]:
        blocked = {r["ngo_id"] for r in self.db.all("SELECT ngo_id FROM blocks WHERE company_id = ?", (company_id,))}
        out = []
        for r in self.db.all("SELECT ngo_id FROM ngo_profiles WHERE listed_publicly = 1 ORDER BY name"):
            ngo = self.ngo_profile(r["ngo_id"])
            if ngo["ngo_id"] in blocked:
                continue  # this company reported the NGO (hidden for this company only)
            listing = self.public_listing(ngo)
            # filters only apply to fields the NGO chose to list
            if sector and sector not in (listing.get("causes") or []):
                continue
            if state and listing.get("state") != state:
                continue
            if district and (listing.get("district") or "").lower() != district.lower():
                continue
            out.append(listing)
        return out

    def match_company_by_cin(self, cin: str) -> dict | None:
        r = self.db.one("SELECT company_id, name, is_demo FROM company_profiles WHERE cin = ?", ((cin or "").upper(),))
        if not r:
            return None
        st = self.verification.status("company", r["company_id"])
        return {"company_id": r["company_id"], "name": r["name"], "is_demo": bool(r["is_demo"]),
                "verification": {"level": st["level"], "badge": st["badge"]}}

    # -- NGO follow-ups from in-app threads (SQLite event log, not memory search) -----------------
    def ngo_thread_history(self, ngo_id: str, pitch_id: str):
        p = self.pitches.get_for_ngo(pitch_id, ngo_id)
        return self.pitches.followup_events(p, self.company_profile(p["company_id"])["name"])

    def ngo_thread_followups(self, ngo_id: str, tracker) -> list[dict]:
        out = []
        for p in self.pitches.list_for_ngo(ngo_id):
            events = self.pitches.followup_events(p, p["company_name"])
            step = tracker.next_step(events)
            if step.action in ("nudge", "new_angle_or_other_company", "promise_reminder"):
                self.pitches.record_nudge(p, step.action, step.due)
                events = self.pitches.followup_events(p, p["company_name"])
            out.append({"key": f"pitch:{p['id']}", "company_id": p["company_id"], "company_name": p["company_name"],
                        "channel": "in_app", "pitch_id": p["id"], "status": p["status"],
                        "events": [e.model_dump() for e in events], "next_step": step.model_dump()})
        return out
