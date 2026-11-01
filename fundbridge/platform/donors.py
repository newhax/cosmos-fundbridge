"""Donor side: profiles, discovery (Matcher), shortlist, follows + feed, self-reported gifts, reports.

No payment processing anywhere: "Give" only returns the NGO's own https donation page plus a notice.
India / INR only. Exact data lives in SQLite; each donor action is also written in plain language to
the donor's private Meko conversation (scope "donor") for recall and traces.
"""
from __future__ import annotations

import ipaddress
import json
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException

from ..agents.matcher import FREQUENCIES, GIVING_RANGES, recommend
from ..formatting import inr
from ..normalize import INDIAN_STATES
from ..sectors import SECTORS
from ..stores.memory_base import MemoryStore
from .db import loads, new_id, now_iso

GIVE_NOTICE = ("You are leaving FundBridge. Payment, receipt and 80G are handled by the NGO. "
               "Ask for an 80G receipt. This is not tax advice.")
INDIA_ONLY = "India and INR only in this demo. FundBridge does not handle foreign donations or FCRA."
MAX_GIFT_INR = 10_000_000


def validate_donation_url(v: str | None) -> str:
    """Only public https URLs (no credentials, IPs or localhost). Empty clears it."""
    v = (v or "").strip()
    if not v:
        return ""
    u = urlparse(v)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or "." not in host or u.username or u.password or len(v) > 500:
        raise HTTPException(422, "Donation URL must be a public https:// link to the NGO's own page.")
    try:
        ipaddress.ip_address(host)
        raise HTTPException(422, "Donation URL must use a domain name, not an IP address.")
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise HTTPException(422, "Donation URL must be public.")
    return v


class DonorService:
    def __init__(self, platform, memory: MemoryStore):
        self.p, self.db, self.memory = platform, platform.db, memory

    # -- memory: plain-language entries in the donor's private conversation -----------------
    def remember(self, donor_id: str, type_: str, summary: str, ngo_id: str | None = None, data: dict | None = None) -> None:
        self.memory.log_thread("donor", donor_id, f"Donor {donor_id} private", {
            "at": now_iso(), "type": type_, "summary": summary, "ngo_id": ngo_id, "actor_role": "donor", "data": data or {}})

    def recall(self, donor_id: str) -> list[dict]:
        return self.memory.read_thread("donor", donor_id)

    # -- profile ----------------------------------------------------------------------------
    def create(self, user_id: str, display_name: str, is_demo: bool = False, **fields) -> str:
        donor_id = new_id("don")
        self.db.run("INSERT INTO donor_profiles (donor_id, user_id, display_name, is_demo, updated_at) VALUES (?,?,?,?,?)",
                    (donor_id, user_id, display_name.strip()[:80] or "Donor", int(is_demo), now_iso()))
        if fields:
            self.update(donor_id, fields)
        return donor_id

    def profile(self, donor_id: str) -> dict:
        r = self.db.one("SELECT * FROM donor_profiles WHERE donor_id = ?", (donor_id,))
        if not r:
            raise HTTPException(404, "Donor profile not found.")
        r["causes"] = loads(r["causes"], [])
        r["anonymous_by_default"] = bool(r["anonymous_by_default"])
        r["is_demo"] = bool(r["is_demo"])
        return r

    def update(self, donor_id: str, fields: dict) -> dict:
        allowed = {"display_name", "state", "city", "causes", "giving_range", "frequency", "anonymous_by_default"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "state" and v and v not in INDIAN_STATES:
                raise HTTPException(422, f"Choose an Indian state. {INDIA_ONLY}")
            if k == "causes":
                v = json.dumps([c for c in (v or []) if c in SECTORS])
            elif k == "giving_range" and v and v not in GIVING_RANGES:
                raise HTTPException(422, f"giving_range must be one of {list(GIVING_RANGES)}")
            elif k == "frequency" and v and v not in FREQUENCIES:
                raise HTTPException(422, f"frequency must be one of {list(FREQUENCIES)}")
            elif k == "anonymous_by_default":
                v = int(bool(v))
            elif isinstance(v, str):
                v = v.strip()[:80]
            sets.append(f"{k} = ?")
            vals.append(v)
        if sets:
            self.db.run(f"UPDATE donor_profiles SET {', '.join(sets)}, updated_at = ? WHERE donor_id = ?",
                        (*vals, now_iso(), donor_id))
            d = self.profile(donor_id)
            self.remember(donor_id, "preferences",
                          f"On {date.today().isoformat()} the donor set preferences: causes {', '.join(d['causes']) or 'none'}; "
                          f"location {d['city'] or '?'}, {d['state'] or '?'}; giving range {d['giving_range'] or 'not set'}; "
                          f"frequency {d['frequency'] or 'not set'}; "
                          f"{'anonymous to NGOs by default' if d['anonymous_by_default'] else 'name may be shown to NGOs'}.",
                          data={k: d[k] for k in ("causes", "state", "city", "giving_range", "frequency")})
        return self.profile(donor_id)

    # -- discovery -------------------------------------------------------------------------------
    def credential_panel(self, ngo: dict) -> list[dict]:
        def item(field, label, value):
            if not value:
                return {"label": label, "value": None, "status": "unknown"}
            src = self.p.credential_label(ngo, field) if field in ("crn", "darpan_id", "has_12a", "has_80g") else "self-declared"
            return {"label": label, "value": value if isinstance(value, str) else "yes", "status": src}
        years = (datetime.now().year - ngo["founded_year"]) if ngo.get("founded_year") else None
        return [item("crn", "CSR-1 registration", ngo["crn"]), item("darpan_id", "NGO Darpan ID", ngo["darpan_id"]),
                item("has_12a", "12A", ngo["has_12a"]), item("has_80g", "80G", ngo["has_80g"]),
                item("founded_year", "Years active", f"{years} (since {ngo['founded_year']})" if years is not None else None)]

    def _discoverable(self, donor_id: str | None = None) -> list[dict]:
        hidden = {r["ngo_id"] for r in self.db.all(  # NGOs this donor reported are hidden for this donor only
            "SELECT ngo_id FROM ngo_reports WHERE reporter_role = 'donor' AND reporter_id = ?", (donor_id,))}
        out = []
        for r in self.db.all("SELECT ngo_id FROM ngo_profiles WHERE listed_publicly = 1 AND accepts_individual_donations = 1 "
                             "AND donation_url LIKE 'https://%' ORDER BY name"):
            ngo = self.p.ngo_profile(r["ngo_id"])
            if ngo["ngo_id"] in hidden:
                continue
            listing = self.p.public_listing(ngo)  # only fields the NGO chose to list
            listing.update({"suggested_gift_inr": ngo.get("suggested_gift_inr"), "credentials_panel": self.credential_panel(ngo),
                            "has_updates": bool(self.db.one("SELECT 1 AS x FROM ngo_updates WHERE ngo_id = ?", (ngo["ngo_id"],)))})
            out.append(listing)
        return out

    def discover(self, donor_id: str, session, cause: str = "", state: str = "") -> dict:
        donor = self.profile(donor_id)
        memory = self.recall(donor_id)  # recall BEFORE matching; cited per recommendation
        ngos = [n for n in self._discoverable(donor_id)
                if (not cause or cause in (n.get("causes") or [])) and (not state or n.get("state") == state)]
        recs = recommend(session, donor, ngos, memory)
        shortlisted = {r["ngo_id"] for r in self.db.all("SELECT ngo_id FROM donor_shortlist WHERE donor_id = ?", (donor_id,))}
        followed = {r["ngo_id"] for r in self.db.all("SELECT ngo_id FROM donor_follows WHERE donor_id = ?", (donor_id,))}
        for r in recs:
            r["shortlisted"] = r["ngo"]["ngo_id"] in shortlisted
            r["following"] = r["ngo"]["ngo_id"] in followed
        if recs:
            self.remember(donor_id, "matcher_trace",
                          f"On {date.today().isoformat()} the Matcher ranked {len(recs)} NGOs; top: "
                          + "; ".join(f"{r['ngo']['name']} ({r['score']['total']})" for r in recs[:3]) + ".",
                          data={"traces": [{"ngo_id": r["ngo"]["ngo_id"], **r["trace"]} for r in recs[:10]]})
        return {"recommendations": recs, "memory_recalled": len(memory), "notice": INDIA_ONLY}

    def _ngo_name(self, ngo_id: str) -> str:
        return self.p.ngo_profile(ngo_id)["name"]

    def _require_discoverable(self, ngo_id: str) -> dict:
        ngo = self.db.one("SELECT ngo_id FROM ngo_profiles WHERE ngo_id = ? AND listed_publicly = 1 "
                          "AND accepts_individual_donations = 1", (ngo_id,))
        if not ngo:
            raise HTTPException(404, "NGO not found.")
        return self.p.ngo_profile(ngo_id)

    # -- shortlist / follow / feed ------------------------------------------------------------------
    def shortlist(self, donor_id: str, ngo_id: str, add: bool) -> None:
        ngo = self._require_discoverable(ngo_id)
        if add:
            self.db.run("INSERT OR IGNORE INTO donor_shortlist VALUES (?,?,?)", (donor_id, ngo_id, now_iso()))
        else:
            self.db.run("DELETE FROM donor_shortlist WHERE donor_id = ? AND ngo_id = ?", (donor_id, ngo_id))
        self.remember(donor_id, "shortlist", f"On {date.today().isoformat()} the donor "
                      f"{'shortlisted' if add else 'removed from the shortlist'} {ngo['name']}.", ngo_id)

    def follow(self, donor_id: str, ngo_id: str, add: bool, share_name: bool | None = None) -> None:
        ngo = self._require_discoverable(ngo_id)
        if add:
            share = (not self.profile(donor_id)["anonymous_by_default"]) if share_name is None else share_name
            self.db.run("INSERT OR REPLACE INTO donor_follows VALUES (?,?,?,?)", (donor_id, ngo_id, int(share), now_iso()))
        else:
            self.db.run("DELETE FROM donor_follows WHERE donor_id = ? AND ngo_id = ?", (donor_id, ngo_id))
        self.remember(donor_id, "follow", f"On {date.today().isoformat()} the donor "
                      f"{'started following' if add else 'stopped following'} {ngo['name']}.", ngo_id)

    def lists(self, donor_id: str) -> dict:
        def names(table):
            return [{"ngo_id": r["ngo_id"], "name": self._ngo_name(r["ngo_id"]), "since": r["created_at"][:10]}
                    for r in self.db.all(f"SELECT * FROM {table} WHERE donor_id = ? ORDER BY created_at DESC", (donor_id,))]
        return {"shortlist": names("donor_shortlist"), "following": names("donor_follows")}

    def feed(self, donor_id: str) -> list[dict]:
        return [dict(r) | {"label": "reported by the NGO"} for r in self.db.all(
            "SELECT u.id, u.ngo_id, n.name AS ngo_name, n.is_demo, u.title, u.body, u.created_at FROM ngo_updates u "
            "JOIN donor_follows f ON f.ngo_id = u.ngo_id AND f.donor_id = ? JOIN ngo_profiles n ON n.ngo_id = u.ngo_id "
            "ORDER BY u.created_at DESC LIMIT 100", (donor_id,))]

    # -- give / gifts / report -----------------------------------------------------------------------
    def give(self, ngo_id: str) -> dict:
        """No payment API: return the NGO's own https page and the leaving-FundBridge notice."""
        ngo = self._require_discoverable(ngo_id)
        return {"url": validate_donation_url(ngo["donation_url"]), "notice": GIVE_NOTICE, "india_only": INDIA_ONLY,
                "ngo_name": ngo["name"]}

    def report_gift(self, donor_id: str, ngo_id: str, amount: float, on: str, share_name: bool | None) -> dict:
        ngo = self._require_discoverable(ngo_id)
        if not (0 < float(amount) <= MAX_GIFT_INR):
            raise HTTPException(422, "Enter the amount in INR.")
        try:
            d = date.fromisoformat(on)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD")
        if d > datetime.now(timezone.utc).date():
            raise HTTPException(422, "The date cannot be in the future.")
        share = (not self.profile(donor_id)["anonymous_by_default"]) if share_name is None else share_name
        gid = new_id("gift")
        self.db.run("INSERT INTO donor_gifts VALUES (?,?,?,?,?,?,?,?)",
                    (gid, donor_id, ngo_id, float(amount), d.isoformat(), "self-reported", int(share), now_iso()))
        self.remember(donor_id, "gift", f"On {d.isoformat()} the donor reported giving {inr(float(amount))} to {ngo['name']} "
                      "(self-reported).", ngo_id, {"gift_id": gid, "amount_inr": float(amount), "date": d.isoformat()})
        return self.db.one("SELECT * FROM donor_gifts WHERE id = ?", (gid,))

    def gifts(self, donor_id: str) -> list[dict]:
        return [g | {"ngo_name": self._ngo_name(g["ngo_id"])} for g in
                self.db.all("SELECT * FROM donor_gifts WHERE donor_id = ? ORDER BY date DESC", (donor_id,))]

    def report_ngo(self, reporter_role: str, reporter_id: str, ngo_id: str, reason: str) -> None:
        self.p.ngo_profile(ngo_id)  # 404 if unknown
        self.db.run("INSERT INTO ngo_reports VALUES (?,?,?,?,?,?,0)",
                    (new_id("nrp"), reporter_role, reporter_id, ngo_id, (reason or "")[:1000], now_iso()))

    # -- NGO side --------------------------------------------------------------------------------------
    def supporters(self, ngo_id: str) -> dict:
        """Aggregate counts; names only for donors who opted in for this NGO."""
        follows = self.db.all("SELECT f.share_name, d.display_name FROM donor_follows f "
                              "JOIN donor_profiles d ON d.donor_id = f.donor_id WHERE f.ngo_id = ?", (ngo_id,))
        gifts = self.db.all("SELECT g.id, g.amount_inr, g.date, g.status, g.share_name, d.display_name FROM donor_gifts g "
                            "JOIN donor_profiles d ON d.donor_id = g.donor_id WHERE g.ngo_id = ? ORDER BY g.date DESC", (ngo_id,))
        return {
            "followers": len(follows),
            "named_followers": sorted({f["display_name"] for f in follows if f["share_name"]}),
            "gifts_reported": len(gifts),
            "gifts_total_inr": sum(g["amount_inr"] for g in gifts),
            "gifts": [{"id": g["id"], "amount_inr": g["amount_inr"], "date": g["date"], "status": g["status"],
                       "donor": g["display_name"] if g["share_name"] else "Anonymous donor"} for g in gifts],
            "note": "Self-reported by donors. FundBridge does not process or see payments.",
        }

    def confirm_gift(self, ngo_id: str, gift_id: str) -> None:
        if not self.db.run("UPDATE donor_gifts SET status = 'confirmed by NGO' WHERE id = ? AND ngo_id = ?", (gift_id, ngo_id)):
            raise HTTPException(404, "Gift not found.")

    def post_update(self, ngo_id: str, title: str, body: str) -> dict:
        if not self.p.ngo_profile(ngo_id)["impact_updates_enabled"]:
            raise HTTPException(403, "Turn on impact updates in your profile first.")
        uid = new_id("upd")
        self.db.run("INSERT INTO ngo_updates VALUES (?,?,?,?,?)", (uid, ngo_id, title.strip()[:200], body.strip()[:5000], now_iso()))
        return self.db.one("SELECT * FROM ngo_updates WHERE id = ?", (uid,)) | {"label": "reported by the NGO"}

    def updates(self, ngo_id: str) -> list[dict]:
        return [r | {"label": "reported by the NGO"} for r in
                self.db.all("SELECT * FROM ngo_updates WHERE ngo_id = ? ORDER BY created_at DESC", (ngo_id,))]
