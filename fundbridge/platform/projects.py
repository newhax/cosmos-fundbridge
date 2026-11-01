"""Projects, calls for proposals, funding requests and project management.

  project   an NGO's funding need: title, sectors, areas (LGD), budget, duration. Listed for companies.
  call      a company's Request for Proposals (RFP): sectors, focus areas, budget range, deadline.
  request   an NGO links a project to a company, either as a Request for Funds (kind 'rff', unsolicited)
            or as a proposal to a call (kind 'proposal'). The company is notified and accepts or rejects.
  engagement  an accepted request. Both sides manage it here: the company sets milestones, the NGO marks
            them done and posts progress and fund-utilisation updates; the company closes it as completed.

Nothing is sent automatically and FundBridge never moves money: amounts are what the parties state.
Every project, call and ranking records the geo_version its areas were checked against.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fastapi import HTTPException

from ..agents.screener import NEUTRAL, ticket_component
from ..formatting import inr
from ..sectors import clean_causes
from . import areas as A
from .db import loads, new_id, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, ngo_id TEXT NOT NULL REFERENCES ngo_profiles(ngo_id), title TEXT NOT NULL,
    sectors TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '', beneficiaries TEXT DEFAULT '',
    budget_inr REAL, duration_months INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'funded', 'closed')),
    geo_version INTEGER, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_projects_ngo ON projects(ngo_id);
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES company_profiles(company_id), title TEXT NOT NULL,
    sectors TEXT NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '', budget_min REAL, budget_max REAL,
    deadline TEXT, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    geo_version INTEGER, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_calls_company ON calls(company_id);
CREATE TABLE IF NOT EXISTS project_requests (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('rff', 'proposal')),
    project_id TEXT NOT NULL REFERENCES projects(id), ngo_id TEXT NOT NULL, company_id TEXT NOT NULL, call_id TEXT,
    amount_inr REAL, message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'withdrawn', 'completed')),
    decision_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, decided_at TEXT);
CREATE INDEX IF NOT EXISTS ix_requests_company ON project_requests(company_id, status);
CREATE INDEX IF NOT EXISTS ix_requests_ngo ON project_requests(ngo_id, status);
CREATE TABLE IF NOT EXISTS project_updates (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES project_requests(id), author_role TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('milestone', 'progress', 'utilisation', 'note')),
    title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', amount_inr REAL, percent INTEGER, due_date TEXT,
    done INTEGER NOT NULL DEFAULT 0, done_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_updates_request ON project_updates(request_id);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL,
    tab TEXT NOT NULL DEFAULT '', read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, read);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY, user_id TEXT, role TEXT, rating INTEGER, category TEXT NOT NULL, message TEXT NOT NULL,
    page TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'en', state_lgd TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'read', 'done')), created_at TEXT NOT NULL);
"""

PROJECT_WEIGHTS = {"sector_fit": 0.45, "area_fit": 0.35, "budget_fit": 0.20}
CALL_WEIGHTS = {"sector_fit": 0.55, "area_fit": 0.45}
FEEDBACK_CATEGORIES = ("bug", "idea", "data", "language", "other")


def _text(v, field: str, lo: int, hi: int) -> str:
    v = (v or "").strip()
    if not lo <= len(v) <= hi:
        raise HTTPException(422, f"{field} must be {lo}-{hi} characters.")
    return v


def _money(v, field: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError) as e:
        raise HTTPException(422, f"{field} must be a number (INR).") from e
    if v <= 0:
        raise HTTPException(422, f"{field} must be positive (INR).")
    return v


def _sectors(v) -> list[str]:
    try:
        out = clean_causes(v)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not out:
        raise HTTPException(422, "Pick at least one sector.")
    return out


def sector_fit(want: list[str], have: list[str]) -> tuple[float | None, list[str]]:
    want, have = {s.lower() for s in want or []}, [s.lower() for s in have or []]
    if not want or not have:
        return None, []
    hit = [s for s in have if s in want]
    return len(hit) / len(set(have)), hit


class ProjectService:
    def __init__(self, platform):
        self.p, self.db = platform, platform.db

    @property
    def geo(self):
        return self.p.geo

    # -- notifications ---------------------------------------------------------------------------------------
    def notify(self, user_id: str | None, text: str, tab: str = "") -> None:
        if user_id:
            self.db.run("INSERT INTO notifications VALUES (?,?,?,?,0,?)", (new_id("ntf"), user_id, text[:300], tab, now_iso()))

    def notifications(self, user_id: str, limit: int = 30) -> dict:
        items = self.db.all("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        unread = self.db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read = 0", (user_id,))["n"]
        return {"unread": unread, "items": [{**i, "read": bool(i["read"])} for i in items]}

    def mark_read(self, user_id: str) -> None:
        self.db.run("UPDATE notifications SET read = 1 WHERE user_id = ?", (user_id,))

    def _user_of(self, entity_type: str, entity_id: str) -> str | None:
        t, k = ("ngo_profiles", "ngo_id") if entity_type == "ngo" else ("company_profiles", "company_id")
        r = self.db.one(f"SELECT user_id FROM {t} WHERE {k} = ?", (entity_id,))
        return r["user_id"] if r else None

    # -- projects (NGO funding needs) ---------------------------------------------------------------------------
    def _project_out(self, r: dict, viewer: str = "ngo") -> dict:
        out = {**r, "sectors": loads(r["sectors"], []), "is_demo": bool(r["is_demo"]),
               "areas": A.get_areas(self.db, self.geo, "project", r["id"], "focus")}
        ngo = self.p.ngo_profile(r["ngo_id"])
        out["ngo_name"], out["ngo_is_demo"] = ngo["name"], ngo["is_demo"]
        if viewer == "ngo":
            out["requests"] = self.db.all(
                "SELECT r.id, r.kind, r.status, r.amount_inr, r.created_at, c.name AS company_name FROM project_requests r "
                "JOIN company_profiles c ON c.company_id = r.company_id WHERE r.project_id = ? ORDER BY r.created_at DESC", (r["id"],))
        return out

    def save_project(self, ngo_id: str, fields: dict, project_id: str | None = None) -> dict:
        cur = self.project_for_ngo(project_id, ngo_id) if project_id else None
        f = {
            "title": _text(fields.get("title", cur and cur["title"]), "Title", 5, 120),
            "sectors": json.dumps(_sectors(fields.get("sectors", cur and cur["sectors"]))),
            "summary": _text(fields.get("summary", cur and cur["summary"]), "Summary", 20, 2000),
            "beneficiaries": (fields.get("beneficiaries", cur and cur["beneficiaries"]) or "").strip()[:300],
            "budget_inr": _money(fields.get("budget_inr", cur and cur["budget_inr"]), "Budget"),
            "duration_months": fields.get("duration_months", cur and cur["duration_months"]) or None,
        }
        if f["duration_months"] is not None and not 1 <= int(f["duration_months"]) <= 120:
            raise HTTPException(422, "Duration must be 1-120 months.")
        status = fields.get("status", cur["status"] if cur else "open")
        if status not in ("open", "funded", "closed"):
            raise HTTPException(422, "Status must be open, funded or closed.")
        areas = A.clean(self.geo, fields["areas"], "focus") if "areas" in fields else None
        if not cur and not areas:
            raise HTTPException(422, "Add where the project runs (district, whole state or all India).")
        now = now_iso()
        if cur:
            self.db.run("UPDATE projects SET title=?, sectors=?, summary=?, beneficiaries=?, budget_inr=?, duration_months=?, "
                        "status=?, geo_version=?, updated_at=? WHERE id=?",
                        (*f.values(), status, self.geo.version, now, project_id))
            pid = project_id
        else:
            pid = new_id("prj")
            ngo = self.p.ngo_profile(ngo_id)
            self.db.run("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, ngo_id, f["title"], f["sectors"], f["summary"], f["beneficiaries"], f["budget_inr"],
                         f["duration_months"], status, self.geo.version, int(ngo["is_demo"]), now, now))
        if areas is not None:
            A.set_areas(self.db, "project", pid, "focus", areas, self.geo.version)
        return self._project_out(self.db.one("SELECT * FROM projects WHERE id = ?", (pid,)))

    def project_for_ngo(self, project_id: str, ngo_id: str) -> dict:
        r = self.db.one("SELECT * FROM projects WHERE id = ? AND ngo_id = ?", (project_id, ngo_id))
        if not r:
            raise HTTPException(404, "Project not found.")
        return r

    def ngo_projects(self, ngo_id: str) -> list[dict]:
        return [self._project_out(r) for r in self.db.all("SELECT * FROM projects WHERE ngo_id = ? ORDER BY updated_at DESC", (ngo_id,))]

    def project_fit(self, company: dict, focus_areas: list[dict], project: dict) -> dict:
        reasons, unknowns, comps = [], [], {}
        s, hit = sector_fit(company.get("focus_sectors"), project["sectors"])
        comps["sector_fit"] = NEUTRAL if s is None else s
        (unknowns.append("Your focus sectors are not set") if s is None else
         reasons.append(f"Sectors in your focus: {', '.join(hit)}" if hit else "No sectors in your focus"))
        m = A.area_match(focus_areas, project["areas"])
        comps["area_fit"] = NEUTRAL if m["score"] is None else m["score"]
        (unknowns.append("Your focus areas are not set") if m["score"] is None else reasons.append(m["note"]))
        t, t_unknown = ticket_component(project.get("budget_inr"), company.get("min_ticket"), company.get("max_ticket"))
        comps["budget_fit"] = t
        if t_unknown:
            unknowns.append(t_unknown)
        else:
            reasons.append(f"Budget {inr(project['budget_inr'])} vs your range {inr(company.get('min_ticket'))} to "
                           f"{inr(company.get('max_ticket'))}")
        total = round(sum(comps[k] * w for k, w in PROJECT_WEIGHTS.items()) * 100, 1)
        return {"total": total, "components": {k: round(v, 4) for k, v in comps.items()}, "weights": PROJECT_WEIGHTS,
                "reasons": reasons, "unknowns": unknowns, "area_level": m["level"]}

    def browse_projects(self, company_id: str, sector: str = "", state: str = "", page: int = 1, per_page: int = 20) -> dict:
        company = self.p.company_profile(company_id)
        focus = A.get_areas(self.db, self.geo, "company", company_id, "focus")
        blocked = {r["ngo_id"] for r in self.db.all("SELECT ngo_id FROM blocks WHERE company_id = ?", (company_id,))}
        mine = {r["project_id"]: r["status"] for r in self.db.all(
            "SELECT project_id, status FROM project_requests WHERE company_id = ?", (company_id,))}
        out = []
        for r in self.db.all("SELECT * FROM projects WHERE status = 'open' ORDER BY updated_at DESC"):
            if r["ngo_id"] in blocked:
                continue
            pr = self._project_out(r, viewer="company")
            if sector and sector not in pr["sectors"]:
                continue
            if state and not any(a.get("state") == state or a["level"] == "all_india" for a in pr["areas"]):
                continue
            pr["fit"] = self.project_fit(company, focus, pr)
            pr["request_status"] = mine.get(r["id"])
            out.append(pr)
        out.sort(key=lambda x: (-x["fit"]["total"], x["title"]))
        return _page(out, page, per_page) | {"geo_version": self.geo.version}

    # -- calls (company RFPs) ---------------------------------------------------------------------------------
    def _call_out(self, r: dict, with_counts: bool = False) -> dict:
        out = {**r, "sectors": loads(r["sectors"], []), "is_demo": bool(r["is_demo"]),
               "areas": A.get_areas(self.db, self.geo, "call", r["id"], "focus")}
        c = self.p.company_profile(r["company_id"])
        out["company_name"], out["company_is_demo"] = c["name"], c["is_demo"]
        out["is_open"] = r["status"] == "open" and (not r["deadline"] or r["deadline"] >= date.today().isoformat())
        if with_counts:
            out["proposals"] = self.db.one("SELECT COUNT(*) AS n FROM project_requests WHERE call_id = ?", (r["id"],))["n"]
        return out

    def create_call(self, company_id: str, fields: dict) -> dict:
        lo, hi = _money(fields.get("budget_min"), "Minimum budget"), _money(fields.get("budget_max"), "Maximum budget")
        if lo and hi and lo > hi:
            raise HTTPException(422, "Minimum budget is above the maximum.")
        deadline = (fields.get("deadline") or "").strip() or None
        if deadline:
            try:
                if date.fromisoformat(deadline) < date.today():
                    raise HTTPException(422, "The deadline is in the past.")
            except ValueError as e:
                raise HTTPException(422, "Deadline must be a date (YYYY-MM-DD).") from e
        areas = A.clean(self.geo, fields.get("areas"), "focus")
        cid = new_id("call")
        company = self.p.company_profile(company_id)
        self.db.run("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, company_id, _text(fields.get("title"), "Title", 5, 120), json.dumps(_sectors(fields.get("sectors"))),
                     _text(fields.get("description"), "Description", 20, 3000), lo, hi, deadline, "open", self.geo.version,
                     int(company["is_demo"]), now_iso()))
        A.set_areas(self.db, "call", cid, "focus", areas, self.geo.version)
        return self._call_out(self.db.one("SELECT * FROM calls WHERE id = ?", (cid,)), True)

    def close_call(self, company_id: str, call_id: str) -> dict:
        if not self.db.run("UPDATE calls SET status = 'closed' WHERE id = ? AND company_id = ?", (call_id, company_id)):
            raise HTTPException(404, "Call not found.")
        return self._call_out(self.db.one("SELECT * FROM calls WHERE id = ?", (call_id,)), True)

    def company_calls(self, company_id: str) -> list[dict]:
        return [self._call_out(r, True) for r in self.db.all(
            "SELECT * FROM calls WHERE company_id = ? ORDER BY created_at DESC", (company_id,))]

    def open_calls(self, ngo_id: str, page: int = 1, per_page: int = 20) -> dict:
        ngo = self.p.ngo_profile(ngo_id)
        mine = A.get_areas(self.db, self.geo, "ngo", ngo_id, "operating") or A.get_areas(self.db, self.geo, "ngo", ngo_id, "hq")
        out = []
        for r in self.db.all("SELECT * FROM calls WHERE status = 'open' ORDER BY created_at DESC"):
            c = self._call_out(r)
            if not c["is_open"]:
                continue
            s, hit = sector_fit(c["sectors"], ngo["causes"])
            m = A.area_match(mine, c["areas"])
            comps = {"sector_fit": NEUTRAL if s is None else s, "area_fit": NEUTRAL if m["score"] is None else m["score"]}
            reasons = ([f"Your causes in this call: {', '.join(hit)}"] if hit else ["None of your causes are in this call"]) \
                if s is not None else []
            reasons.append(m["note"])
            c["fit"] = {"total": round(sum(comps[k] * w for k, w in CALL_WEIGHTS.items()) * 100, 1),
                        "components": comps, "weights": CALL_WEIGHTS, "reasons": reasons,
                        "unknowns": (["Your causes are not set"] if s is None else []) + (["Your areas are not set"] if m["score"] is None else [])}
            out.append(c)
        out.sort(key=lambda x: (-x["fit"]["total"], x["deadline"] or "9999"))
        return _page(out, page, per_page) | {"geo_version": self.geo.version}

    # -- requests: RFF + proposals -------------------------------------------------------------------------------
    def _new_request(self, kind: str, ngo_id: str, project_id: str, company_id: str, amount, message: str,
                     call_id: str | None = None) -> dict:
        prj = self.project_for_ngo(project_id, ngo_id)
        if prj["status"] != "open":
            raise HTTPException(409, "Only open projects can be sent.")
        company = self.db.one("SELECT * FROM company_profiles WHERE company_id = ?", (company_id,))
        if not company:
            raise HTTPException(404, "Company not found.")
        if self.db.one("SELECT 1 AS x FROM blocks WHERE company_id = ? AND ngo_id = ?", (company_id, ngo_id)):
            raise HTTPException(403, "This company is not accepting requests from your organisation.")
        if self.db.one("SELECT 1 AS x FROM project_requests WHERE project_id = ? AND company_id = ? AND status = 'pending' "
                       "AND COALESCE(call_id, '') = ?", (project_id, company_id, call_id or "")):
            raise HTTPException(409, "This project is already waiting for this company's answer.")
        day = datetime.now(timezone.utc).date().isoformat()
        sent = self.db.one("SELECT COUNT(*) AS n FROM project_requests WHERE ngo_id = ? AND created_at >= ?", (ngo_id, day))["n"]
        if sent >= self.p.settings.pitches_per_ngo_per_day:
            raise HTTPException(429, f"Daily limit reached: {self.p.settings.pitches_per_ngo_per_day} requests per day.")
        rid = new_id("req")
        self.db.run("INSERT INTO project_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, kind, project_id, ngo_id, company_id, call_id, _money(amount, "Amount"),
                     (message or "").strip()[:3000], "pending", "", now_iso(), None))
        ngo = self.p.ngo_profile(ngo_id)
        what = "a request for funds" if kind == "rff" else "a proposal to your call"
        self.notify(company["user_id"], f"{ngo['name']} sent {what}: {prj['title']}", "projects")
        return self.request_view(rid)

    def request_funds(self, ngo_id: str, project_id: str, company_id: str, amount, message: str) -> dict:
        return self._new_request("rff", ngo_id, project_id, company_id, amount, message)

    def submit_proposal(self, ngo_id: str, call_id: str, project_id: str, amount, message: str) -> dict:
        call = self.db.one("SELECT * FROM calls WHERE id = ?", (call_id,))
        if not call or not self._call_out(call)["is_open"]:
            raise HTTPException(409, "This call is closed.")
        return self._new_request("proposal", ngo_id, project_id, call["company_id"], amount, message, call_id)

    def request_view(self, rid: str) -> dict:
        r = self.db.one("SELECT r.*, p.title AS project_title, n.name AS ngo_name, n.is_demo AS ngo_is_demo, "
                        "c.name AS company_name, c.is_demo AS company_is_demo, k.title AS call_title FROM project_requests r "
                        "JOIN projects p ON p.id = r.project_id JOIN ngo_profiles n ON n.ngo_id = r.ngo_id "
                        "JOIN company_profiles c ON c.company_id = r.company_id LEFT JOIN calls k ON k.id = r.call_id "
                        "WHERE r.id = ?", (rid,))
        if not r:
            raise HTTPException(404, "Request not found.")
        r["ngo_is_demo"], r["company_is_demo"] = bool(r["ngo_is_demo"]), bool(r["company_is_demo"])
        return r

    def requests_for(self, role: str, entity_id: str, status: str = "") -> list[dict]:
        col = "ngo_id" if role == "ngo" else "company_id"
        q, args = f"SELECT id FROM project_requests WHERE {col} = ?", [entity_id]
        if status:
            q += " AND status = ?"
            args.append(status)
        return [self.request_view(r["id"]) for r in self.db.all(q + " ORDER BY created_at DESC", args)]

    def _own_request(self, rid: str, role: str, entity_id: str) -> dict:
        r = self.request_view(rid)
        if r["ngo_id" if role == "ngo" else "company_id"] != entity_id:
            raise HTTPException(404, "Request not found.")
        return r

    def decide(self, company_id: str, rid: str, accept: bool, note: str = "") -> dict:
        r = self._own_request(rid, "company", company_id)
        if r["status"] != "pending":
            raise HTTPException(409, f"This request is already {r['status']}.")
        self.db.run("UPDATE project_requests SET status = ?, decision_note = ?, decided_at = ? WHERE id = ?",
                    ("accepted" if accept else "rejected", (note or "").strip()[:1000], now_iso(), rid))
        verb = "accepted" if accept else "declined"
        self.notify(self._user_of("ngo", r["ngo_id"]), f"{r['company_name']} {verb} {r['project_title']}"
                    + (f": {note.strip()[:120]}" if (note or "").strip() else ""), "projects")
        return self.request_view(rid)

    def withdraw(self, ngo_id: str, rid: str) -> dict:
        r = self._own_request(rid, "ngo", ngo_id)
        if r["status"] != "pending":
            raise HTTPException(409, "Only pending requests can be withdrawn.")
        self.db.run("UPDATE project_requests SET status = 'withdrawn', decided_at = ? WHERE id = ?", (now_iso(), rid))
        return self.request_view(rid)

    # -- engagements (accepted requests): project management ------------------------------------------------------
    def engagements(self, role: str, entity_id: str) -> list[dict]:
        out = []
        col = "ngo_id" if role == "ngo" else "company_id"
        for r in self.db.all(f"SELECT id FROM project_requests WHERE {col} = ? AND status IN ('accepted', 'completed') "
                             "ORDER BY decided_at DESC", (entity_id,)):
            out.append(self.engagement(r["id"], role, entity_id))
        return out

    def engagement(self, rid: str, role: str, entity_id: str) -> dict:
        r = self._own_request(rid, role, entity_id)
        if r["status"] not in ("accepted", "completed"):
            raise HTTPException(409, "Only accepted requests are managed here.")
        ups = self.db.all("SELECT * FROM project_updates WHERE request_id = ? ORDER BY created_at", (rid,))
        for u in ups:
            u["done"] = bool(u["done"])
        milestones = [u for u in ups if u["kind"] == "milestone"]
        progress = [u["percent"] for u in ups if u["kind"] == "progress" and u["percent"] is not None]
        used = sum(u["amount_inr"] or 0 for u in ups if u["kind"] == "utilisation")
        return {**r, "updates": ups,
                "summary": {"milestones": len(milestones), "milestones_done": sum(m["done"] for m in milestones),
                            "latest_progress": progress[-1] if progress else None,
                            "utilised_inr": used, "agreed_inr": r["amount_inr"],
                            "overdue": [m["title"] for m in milestones
                                        if not m["done"] and m["due_date"] and m["due_date"] < date.today().isoformat()]}}

    def add_update(self, rid: str, role: str, entity_id: str, fields: dict) -> dict:
        e = self.engagement(rid, role, entity_id)
        if e["status"] == "completed":
            raise HTTPException(409, "This project is completed.")
        kind = fields.get("kind")
        allowed = {"company": ("milestone", "note"), "ngo": ("progress", "utilisation", "note")}[role]
        if kind not in allowed:
            raise HTTPException(422, f"As {'a company' if role == 'company' else 'an NGO'} you can add: {', '.join(allowed)}.")
        pct = fields.get("percent")
        if kind == "progress":
            if pct in (None, "") or not 0 <= int(pct) <= 100:
                raise HTTPException(422, "Progress needs a percentage from 0 to 100.")
            pct = int(pct)
        amount = _money(fields.get("amount_inr"), "Amount") if kind == "utilisation" else None
        if kind == "utilisation" and amount is None:
            raise HTTPException(422, "Utilisation needs the amount spent (INR).")
        due = (fields.get("due_date") or "").strip() or None
        if due:
            try:
                date.fromisoformat(due)
            except ValueError as err:
                raise HTTPException(422, "Due date must be a date (YYYY-MM-DD).") from err
        uid = new_id("upd")
        self.db.run("INSERT INTO project_updates VALUES (?,?,?,?,?,?,?,?,?,0,NULL,?)",
                    (uid, rid, role, kind, _text(fields.get("title"), "Title", 3, 160), (fields.get("body") or "").strip()[:3000],
                     amount, pct if kind == "progress" else None, due if kind == "milestone" else None, now_iso()))
        other = self._user_of("company", e["company_id"]) if role == "ngo" else self._user_of("ngo", e["ngo_id"])
        who = e["ngo_name"] if role == "ngo" else e["company_name"]
        self.notify(other, f"{who} added a {kind} to {e['project_title']}", "projects")
        return self.engagement(rid, role, entity_id)

    def set_milestone_done(self, rid: str, update_id: str, ngo_id: str, done: bool) -> dict:
        e = self.engagement(rid, "ngo", ngo_id)
        if not self.db.run("UPDATE project_updates SET done = ?, done_at = ? WHERE id = ? AND request_id = ? AND kind = 'milestone'",
                           (int(done), now_iso() if done else None, update_id, rid)):
            raise HTTPException(404, "Milestone not found.")
        if done:
            self.notify(self._user_of("company", e["company_id"]), f"{e['ngo_name']} marked a milestone done on {e['project_title']}",
                        "projects")
        return self.engagement(rid, "ngo", ngo_id)

    def complete(self, rid: str, company_id: str) -> dict:
        e = self.engagement(rid, "company", company_id)
        self.db.run("UPDATE project_requests SET status = 'completed' WHERE id = ?", (rid,))
        self.notify(self._user_of("ngo", e["ngo_id"]), f"{e['company_name']} marked {e['project_title']} as completed", "projects")
        return self.engagement(rid, "company", company_id)

    # -- feedback --------------------------------------------------------------------------------------------
    def add_feedback(self, user: dict | None, fields: dict) -> dict:
        cat = fields.get("category") or "other"
        if cat not in FEEDBACK_CATEGORIES:
            raise HTTPException(422, f"Category must be one of {', '.join(FEEDBACK_CATEGORIES)}.")
        rating = fields.get("rating")
        if rating not in (None, ""):
            if int(rating) not in range(1, 6):
                raise HTTPException(422, "Rating is 1 to 5.")
            rating = int(rating)
        else:
            rating = None
        fid = new_id("fbk")
        self.db.run("INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, user["id"] if user else None, user["role"] if user else None, rating, cat,
                     _text(fields.get("message"), "Message", 5, 2000), (fields.get("page") or "")[:80],
                     (fields.get("language") or "en")[:5], (fields.get("state") or "")[:5], "new", now_iso()))
        return {"ok": True, "id": fid}


def _page(items: list, page: int, per_page: int) -> dict:
    per_page = max(1, min(int(per_page or 20), 50))
    page = max(1, int(page or 1))
    start = (page - 1) * per_page
    return {"items": items[start:start + per_page], "total": len(items), "page": page, "per_page": per_page,
            "pages": max(1, -(-len(items) // per_page))}
