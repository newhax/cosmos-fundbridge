"""Verification module: automatic checks only (free, no external calls).

The checks are regex formats, duplicate-ID detection across accounts, a CIN join against the ingested
CSR dataset, and a name-consistency check. FundBridge never automates or scrapes captcha-protected
portals and never calls unofficial verification APIs.

Levels: self_declared -> format_checked (NGO: ID formats valid, no duplicate IDs) or evidence_linked
(company: CIN found in the CSR filings with a matching name). Nobody checks official records by hand,
so the word "verified" is never displayed.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

from ..normalize import company_key

PAN_RE = re.compile(r"^[A-Z]{3}[ABCFGHLJPTE][A-Z][0-9]{4}[A-Z]$")
CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}(PLC|PTC|FLC|FTC|GOI|GAP|GAT|NPL|ULL|ULT|OPC|SGC|GOU)[0-9]{6}$")
DARPAN_RE = re.compile(r"^[A-Z]{2}/[0-9]{4}/[0-9]{7}$")

LEVELS = ("self_declared", "format_checked", "evidence_linked")


def valid_pan(v: str) -> bool:
    return bool(PAN_RE.match((v or "").strip().upper()))


def valid_cin(v: str) -> bool:
    return bool(CIN_RE.match((v or "").strip().upper()))


def valid_darpan(v: str) -> bool:
    return bool(DARPAN_RE.match((v or "").strip().upper()))


class VerificationService:
    def __init__(self, platform, csr_store_getter):
        self.p, self.db, self.csr = platform, platform.db, csr_store_getter

    # -- entity access ----------------------------------------------------------------
    def _entity(self, entity_type: str, entity_id: str) -> dict:
        if entity_type == "ngo":
            return self.p.ngo_profile(entity_id)
        if entity_type == "company":
            return self.p.company_profile(entity_id)
        raise HTTPException(404, "Unknown entity type")

    def _ids(self, entity_type: str, e: dict) -> dict[str, str]:
        if entity_type == "ngo":
            return {k: (e.get(k) or "").strip().upper() for k in ("pan", "darpan_id", "crn", "section8_cin") if e.get(k)}
        return {k: (e.get(k) or "").strip().upper() for k in ("cin", "pan") if e.get(k)}

    # -- automatic checks (no network) -------------------------------------------------------
    def auto_checks(self, entity_type: str, entity_id: str) -> list[dict]:
        e = self._entity(entity_type, entity_id)
        ids = self._ids(entity_type, e)
        out = []
        fmt = {"pan": valid_pan, "darpan_id": valid_darpan, "cin": valid_cin, "section8_cin": valid_cin}
        sample = self.csr().data_source() == "SAMPLE"
        for k, v in ids.items():
            if k not in fmt:
                continue
            if sample and v.startswith("SYN"):  # synthetic sample ids never match the real format
                out.append({"check": f"format:{k}", "ok": True, "detail": f"{k} {v} is a SYNTHETIC sample id (format check skipped)"})
            else:
                ok = fmt[k](v)
                out.append({"check": f"format:{k}", "ok": ok, "detail": f"{k} {v} {'has a valid format' if ok else 'has an invalid format'}"})
        table, key = ("ngo_profiles", "ngo_id") if entity_type == "ngo" else ("company_profiles", "company_id")
        for k, v in ids.items():
            dupes = self.db.all(f"SELECT {key} FROM {table} WHERE UPPER(TRIM({k})) = ? AND {key} != ?", (v, entity_id))
            out.append({"check": f"duplicate:{k}", "ok": not dupes,
                        "detail": f"{k} {v} is also used by {len(dupes)} other account(s)" if dupes else f"{k} {v} is not used by any other account"})
        if entity_type == "company":
            out.extend(self._csr_join(e))
        return out

    def _csr_join(self, comp: dict) -> list[dict]:
        cin = (comp.get("cin") or "").upper()
        if not cin:
            return [{"check": "csr_filings", "ok": False, "detail": "No CIN: CSR footprint unknown"}]
        prof = self.csr().company_profile(cin)
        if not prof:
            return [{"check": "csr_filings", "ok": False, "detail": f"CIN {cin} not found in the ingested CSR filings (unknown)"}]
        fys = sorted({r["fy"] for r in prof.get("spend_by_sector_year", [])})
        name_ok = company_key(comp["name"].replace("(DEMO)", "")) == company_key(prof["company_name"])
        return [
            {"check": "csr_filings", "ok": True, "detail": f"Found in CSR filings FY{fys[0]} to FY{fys[-1]} ({len(fys)} years)",
             "source_refs": prof.get("source_refs", [])[:20], "fy_range": [fys[0], fys[-1]] if fys else []},
            {"check": "name_consistency", "ok": name_ok,
             "detail": f"Profile name '{comp['name']}' {'matches' if name_ok else 'does not match'} the filing name '{prof['company_name']}'"},
        ]

    # -- level + badge (recomputed on every read) ----------------------------------------------------
    def status(self, entity_type: str, entity_id: str) -> dict:
        auto = self.auto_checks(entity_type, entity_id)
        warnings = []
        level = "self_declared"
        dupes = [a for a in auto if a["check"].startswith("duplicate:") and not a["ok"]]
        formats = [a for a in auto if a["check"].startswith("format:")]
        if formats and all(a["ok"] for a in formats) and not dupes and entity_type == "ngo":
            level = "format_checked"
        csr = next((a for a in auto if a["check"] == "csr_filings"), None)
        name = next((a for a in auto if a["check"] == "name_consistency"), None)
        if entity_type == "company" and csr and csr["ok"] and name and name["ok"] and not dupes and all(a["ok"] for a in formats):
            level = "evidence_linked"
        if dupes:
            warnings.append("An ID on this profile is also used by another account.")
        return {"entity_type": entity_type, "entity_id": entity_id, "level": level, "badge": self.badge(level),
                "warnings": warnings, "auto_checks": auto}

    @staticmethod
    def badge(level: str) -> dict:
        labels = {
            "self_declared": ("Self-declared", "Details are as entered by the account. Not checked.", "neutral"),
            "format_checked": ("Format checked", "ID formats look valid and are not reused by other accounts. Records not checked.", "neutral"),
            "evidence_linked": ("Found in CSR filings", "The CIN appears in public CSR filings with a matching name. Records not checked.", "info"),
        }
        label, text, tone = labels[level]
        return {"label": label, "text": text, "disclaimer": "", "tone": tone}
