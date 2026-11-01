"""Sector taxonomy: controlled sectors <-> Schedule VII references, and the raw-label mapping.

  fundbridge/schedule_vii.json        shipped reference: Schedule VII items read from an official source
                                      (verified=true) and each sector's item references
  data/processed/sector_overrides.json  admin edits: raw filing label -> sector, and per-sector reference
                                      overrides. Written by the admin screen, read by ingest (so a new dataset
                                      version applies them) and by the API.

Raw labels that fall into "other" form the admin's unmapped-sectors queue (counted on the active dataset).
No legal conclusions: references are shown as "Schedule VII item (ii)" with the source, nothing more.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from .config import PROCESSED_DIR, ROOT
from .sectors import SECTORS

REFERENCE_FILE = ROOT / "fundbridge" / "schedule_vii.json"
OVERRIDES_FILE = PROCESSED_DIR / "sector_overrides.json"
_LOCK = threading.Lock()


def reference() -> dict:
    return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))


def overrides() -> dict:
    if not OVERRIDES_FILE.exists():
        return {"labels": {}, "sector_items": {}}
    return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))


def _write(data: dict) -> None:
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_FILE.with_name(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, OVERRIDES_FILE)


def label_key(raw: str) -> str:
    return " ".join((raw or "").lower().split())


def override_for(raw_label: str) -> str | None:
    """Admin mapping for a raw filing label (applied by ingest before the keyword table)."""
    return overrides()["labels"].get(label_key(raw_label), {}).get("sector")


def set_label(raw_label: str, sector: str, by: str) -> dict:
    if sector not in SECTORS:
        raise ValueError(f"Unknown sector {sector!r}")
    with _LOCK:
        data = overrides()
        data["labels"][label_key(raw_label)] = {"label": raw_label.strip(), "sector": sector, "by": by,
                                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write(data)
    return data["labels"][label_key(raw_label)]


def set_sector_items(sector: str, items: list[str], note: str, by: str) -> dict:
    if sector not in SECTORS:
        raise ValueError(f"Unknown sector {sector!r}")
    known = {i["item"] for i in reference()["items"]}
    clean = [i.strip() for i in items if i.strip()]
    with _LOCK:
        data = overrides()
        data["sector_items"][sector] = {"items": clean, "note": note.strip()[:500], "by": by,
                                        "unverified_items": [i for i in clean if i not in known],
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write(data)
    return data["sector_items"][sector]


def sector_table() -> list[dict]:
    """Every controlled sector with its Schedule VII references and whether each one is verified."""
    ref, ov = reference(), overrides()
    items = {i["item"]: i for i in ref["items"]}
    out = []
    for s in SECTORS:
        o = ov["sector_items"].get(s)
        refs = o["items"] if o else ref["sector_items"].get(s, [])
        out.append({"sector": s, "schedule_vii": [{"item": r, "text": items.get(r, {}).get("text"),
                                                    "verified": bool(items.get(r, {}).get("verified"))} for r in refs],
                    "status": ("unmapped" if not refs else "verified" if all(items.get(r, {}).get("verified") for r in refs)
                               else "not yet checked against the official text"),
                    "edited_by": o["by"] if o else None, "note": (o or {}).get("note", "")})
    return out
