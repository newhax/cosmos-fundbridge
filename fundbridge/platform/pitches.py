"""Pitches and the shared pitch thread.

Every event is written to (a) the SQLite pitch_events log, which is the source of truth for
dates and follow-up rules, and (b) Meko conversations via MemoryStore.log_thread:
  shared events  -> the pitch's own conversation (session_id = pitch id)
  company-only   -> that company's private conversation (e.g. screening results)
  ngo-only       -> that NGO's private conversation (e.g. nudge suggestions)

Isolation: every read takes the caller's ngo_id / company_id from the session; a pitch that
does not belong to the caller is reported as "not found".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from ..config import Settings
from ..models import FollowUpEvent
from ..stores.memory_base import MemoryStore
from .db import EVENT_TYPES, PlatformDB, loads, new_id, now_iso

DECISIONS = {"interested": "interested", "info_requested": "info_requested", "declined": "declined"}
PRIVATE = {"screened": "company", "nudge_suggested": "ngo", "drafted": "ngo"}


def _pitch_row(r: dict) -> dict:
    out = dict(r)
    for k, d in (("evidence_refs", []), ("sectors", [])):
        out[k] = loads(out.get(k), d)
    return out


class PitchService:
    def __init__(self, db: PlatformDB, memory: MemoryStore, settings: Settings):
        self.db, self.memory, self.settings = db, memory, settings

    # -- events ---------------------------------------------------------------------
    def add_event(self, pitch: dict, type_: str, actor_role: str, payload: dict | None = None,
                  visibility: str | None = None, at: str | None = None) -> dict:
        assert type_ in EVENT_TYPES, type_
        vis = visibility or PRIVATE.get(type_, "shared")
        ev = {"id": new_id("pev"), "pitch_id": pitch["id"], "type": type_, "actor_role": actor_role,
              "visibility": vis, "payload": payload or {}, "created_at": at or now_iso()}
        self.db.run("INSERT INTO pitch_events VALUES (?,?,?,?,?,?,?)",
                    (ev["id"], ev["pitch_id"], type_, actor_role, vis, json.dumps(ev["payload"]), ev["created_at"]))
        msg = {"at": ev["created_at"], "event_id": ev["id"], "pitch_id": pitch["id"], "type": type_,
               "actor_role": actor_role, "summary": (payload or {}).get("summary") or (payload or {}).get("text", "")[:300],
               "payload": payload or {}}
        title = f"Pitch {pitch['id']}: {pitch.get('subject', '')[:60]}"
        if vis == "shared":
            self.memory.log_thread("pitch", pitch["id"], title, msg)
        elif vis == "company":
            self.memory.log_thread("company", pitch["company_id"], f"Company {pitch['company_id']} private", msg)
        else:
            self.memory.log_thread("ngo", pitch["ngo_id"], f"NGO {pitch['ngo_id']} private", msg)
        return ev

    def events(self, pitch_id: str, viewer_role: str) -> list[dict]:
        allowed = ("shared", "company") if viewer_role == "company" else ("shared", "ngo")
        rows = self.db.all("SELECT * FROM pitch_events WHERE pitch_id = ? AND visibility IN (?, ?) "
                           "ORDER BY created_at, rowid", (pitch_id, *allowed))
        for r in rows:
            r["payload"] = loads(r["payload"], {})
        return rows

    # -- access (isolation) ------------------------------------------------------------
    def get_for_ngo(self, pitch_id: str, ngo_id: str) -> dict:
        r = self.db.one("SELECT * FROM pitches WHERE id = ? AND ngo_id = ?", (pitch_id, ngo_id))
        if not r:
            raise HTTPException(404, "Pitch not found.")
        return _pitch_row(r)

    def get_for_company(self, pitch_id: str, company_id: str) -> dict:
        r = self.db.one("SELECT * FROM pitches WHERE id = ? AND company_id = ?", (pitch_id, company_id))
        if not r:
            raise HTTPException(404, "Pitch not found.")
        return _pitch_row(r)

    def list_for_ngo(self, ngo_id: str) -> list[dict]:
        return [_pitch_row(r) | {"company_name": r["company_name"]} for r in self.db.all(
            "SELECT p.*, c.name AS company_name FROM pitches p JOIN company_profiles c ON c.company_id = p.company_id "
            "WHERE p.ngo_id = ? ORDER BY p.created_at DESC", (ngo_id,))]

    def list_for_company(self, company_id: str) -> list[dict]:
        return [_pitch_row(r) | {"ngo_name": r["ngo_name"], "ngo_is_demo": bool(r["ngo_is_demo"])} for r in self.db.all(
            "SELECT p.*, n.name AS ngo_name, n.is_demo AS ngo_is_demo FROM pitches p "
            "JOIN ngo_profiles n ON n.ngo_id = p.ngo_id WHERE p.company_id = ? ORDER BY p.created_at DESC", (company_id,))]

    # -- NGO: send ---------------------------------------------------------------------
    def sent_today(self, ngo_id: str) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        return self.db.one("SELECT COUNT(*) AS n FROM pitches WHERE ngo_id = ? AND substr(sent_at, 1, 10) = ?",
                           (ngo_id, day))["n"]

    def create_and_send(self, ngo_id: str, company_id: str, subject: str, body: str, evidence_refs: list[str],
                        ask_inr: float | None, sectors: list[str], state: str, district: str,
                        at: str | None = None) -> dict:
        company = self.db.one("SELECT * FROM company_profiles WHERE company_id = ?", (company_id,))
        if not company:
            raise HTTPException(404, "This company is not on the platform. Reach it with your own email instead.")
        if self.db.one("SELECT 1 AS x FROM blocks WHERE company_id = ? AND ngo_id = ?", (company_id, ngo_id)):
            raise HTTPException(403, "This company is not accepting pitches from your organisation.")
        if self.sent_today(ngo_id) >= self.settings.pitches_per_ngo_per_day:
            raise HTTPException(429, f"Daily limit reached: {self.settings.pitches_per_ngo_per_day} pitches per day.")
        if not subject.strip() or not body.strip():
            raise HTTPException(422, "Subject and body are required.")
        pid, ts = new_id("pit"), at or now_iso()
        self.db.run("INSERT INTO pitches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, ngo_id, company_id, subject.strip(), body.strip(), json.dumps(evidence_refs[:60]), "sent",
                     ask_inr, json.dumps(sectors), state, district, ts, ts, ts))
        pitch = self.get_for_ngo(pid, ngo_id)
        self.add_event(pitch, "drafted", "ngo", {"summary": "Pitch drafted and approved by the NGO."}, at=ts)
        self.add_event(pitch, "sent", "ngo", {"summary": f"Sent in app: {subject.strip()[:120]}"}, at=ts)
        return pitch

    # -- company: view / respond / block ----------------------------------------------------
    def mark_viewed(self, pitch: dict) -> None:
        if not self.db.one("SELECT 1 AS x FROM pitch_events WHERE pitch_id = ? AND type = 'viewed'", (pitch["id"],)):
            self.add_event(pitch, "viewed", "company", {"summary": "Opened by the company."})
            if pitch["status"] == "sent":
                self._set_status(pitch["id"], "viewed")

    def respond(self, pitch: dict, action: str, reply_text: str, promise: dict | None) -> list[dict]:
        if action not in DECISIONS:
            raise HTTPException(422, "action must be interested, info_requested or declined")
        out = [self.add_event(pitch, action, "company", {"summary": {"interested": "Marked: Interested",
                                                                    "info_requested": "Marked: Need more info",
                                                                    "declined": "Marked: Not a fit"}[action]})]
        if reply_text.strip():
            out.append(self.add_event(pitch, "reply", "company", {"text": reply_text.strip()[:4000],
                                                                   "summary": reply_text.strip()[:300]}))
        if promise and (promise.get("what") or "").strip():
            due = promise.get("due_date") or None
            if due:
                try:
                    due = datetime.fromisoformat(due).date().isoformat()
                except ValueError:
                    raise HTTPException(422, "due_date must be YYYY-MM-DD")
            amount = promise.get("amount")
            out.append(self.add_event(pitch, "promise_made", "company", {
                "what": promise["what"].strip()[:500], "amount_inr": float(amount) if amount not in (None, "") else None,
                "due_date": due, "summary": f"Promise: {promise['what'].strip()[:200]} (by {due or 'unknown'})"}))
        self._set_status(pitch["id"], action)
        return out

    def report_and_block(self, pitch: dict, reason: str) -> None:
        """Only affects the reporting company: the NGO can no longer pitch it and is hidden from its directory."""
        self.db.run("INSERT OR IGNORE INTO blocks VALUES (?,?,?)", (pitch["company_id"], pitch["ngo_id"], now_iso()))
        self.db.run("INSERT INTO reports VALUES (?,?,?,?,?,?,0)", (new_id("rep"), pitch["company_id"], pitch["ngo_id"],
                                                                 pitch["id"], (reason or "")[:500], now_iso()))

    def _set_status(self, pitch_id: str, status: str) -> None:
        self.db.run("UPDATE pitches SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), pitch_id))

    def pipeline(self, company_id: str) -> dict:
        pitches = self.list_for_company(company_id)
        cols: dict[str, list] = {s: [] for s in ("sent", "viewed", "info_requested", "interested", "declined")}
        promised_total = 0.0
        for p in pitches:
            promises = [e for e in self.events(p["id"], "company") if e["type"] == "promise_made"]
            last = promises[-1]["payload"] if promises else None
            if last and last.get("amount_inr"):
                promised_total += last["amount_inr"]
            cols.setdefault(p["status"], []).append({
                "id": p["id"], "ngo_name": p["ngo_name"], "ngo_is_demo": p["ngo_is_demo"], "subject": p["subject"],
                "ask_inr": p["ask_inr"], "sent_at": p["sent_at"], "promise": last})
        return {"columns": cols, "promised_total_inr": promised_total, "count": len(pitches)}

    # -- NGO follow-up: thread events -> FollowUpEvents (no pasted replies) -----------------------
    def followup_events(self, pitch: dict, company_name: str) -> list[FollowUpEvent]:
        out = []
        for e in self.events(pitch["id"], "ngo"):
            p = e["payload"]
            base = dict(event_id=e["id"], ngo_id=pitch["ngo_id"], company_id=pitch["company_id"], company_name=company_name,
                        at=e["created_at"], meta={"pitch_id": pitch["id"], "thread_type": e["type"], "in_app": True})
            t = e["type"]
            if t == "reply":
                out.append(FollowUpEvent(type="reply_received", text=p.get("text", ""), **base))
            elif t == "promise_made":
                out.append(FollowUpEvent(type="promise_made", text=p.get("summary", ""), promise_what=p.get("what"),
                                         promise_by=p.get("due_date"), **base | {"meta": base["meta"] | {"amount_inr": p.get("amount_inr")}}))
            elif t in ("drafted", "sent", "viewed", "interested", "info_requested", "declined", "nudge_suggested"):
                out.append(FollowUpEvent(type=t, text=p.get("summary", ""), **base))
        return out

    def record_nudge(self, pitch: dict, action: str, due: str | None) -> None:
        """Log one nudge_suggested event per (pitch, action) so the NGO's private memory has it."""
        for e in self.events(pitch["id"], "ngo"):
            if e["type"] == "nudge_suggested" and e["payload"].get("action") == action:
                return
        self.add_event(pitch, "nudge_suggested", "system", {"action": action, "due": due,
                                                           "summary": f"Suggested next step: {action} (due {due})"})
