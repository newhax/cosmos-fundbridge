"""FastAPI app. Endpoints are sync (run in a threadpool) so stores can block freely.

Every NGO route derives ngo_id from the session cookie (never from the client), so an
NGO can only ever read or write its own runs, drafts, memory and pitches.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.context import Context
from .agents.followup import FollowUpTracker, new_event
from .agents.writer import Writer
from .appstate import AppState
from .config import I18N_LANGUAGES, PLATFORM_DB_PATH, ROOT, SCORE_WEIGHTS, settings
from .llm import Router
from .models import EmailDraft, NGOProfile, RankedFunder
from .pipeline import run_pipeline
from .replay import replay
from .platform import routes as platform_routes
from .platform import routes_geo, routes_projects
from .platform.auth import current_user, require_role
from .platform.service import Platform
from .sectors import SECTORS
from .state import STATE
from .stores import build_backends

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["backends"] = build_backends()
    STATE["llm"] = Router(settings)
    STATE["app"] = AppState()
    STATE["platform"] = Platform(PLATFORM_DB_PATH, STATE["backends"].memory, settings)
    try:  # profiles saved before areas existed get LGD areas (unmatched names go to the admin queue)
        STATE["platform"].backfill_areas()
    except FileNotFoundError as e:
        logging.getLogger("fundbridge.api").warning("geography not loaded (%s); run python scripts/geo.py load", e)
    yield


app = FastAPI(title="FundBridge", lifespan=lifespan)
# Cookies are used for sessions, so only the app's own origin may call the API with credentials.
app.add_middleware(CORSMiddleware, allow_origins=[settings.app_base_url], allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])
app.include_router(platform_routes.router)
app.include_router(routes_projects.router)
app.include_router(routes_geo.router)

NGO = require_role("ngo")


def B():
    return STATE["backends"]


def ctx_for(ngo_id: str, user_id: str | None = None) -> Context:
    ngo = STATE["app"].get_ngo(ngo_id)
    if not ngo:
        raise HTTPException(404, "Run the funder search first.")
    run_id = f"req_{uuid.uuid4().hex[:10]}"
    return Context(ngo=NGOProfile(**ngo), csr=B().csr, memory=B().memory, llm=STATE["llm"].session(run_id, user_id),
                   run_id=run_id, lookback_years=settings.lookback_years)


# -- status / public data ---------------------------------------------------------
@app.get("/api/status")
def status():
    rep = B().csr.data_quality_report()
    router: Router = STATE["llm"]
    st = B().status()
    st["warnings"] = st["warnings"] + router.warnings()
    mem_err = getattr(B().memory, "last_error", None)
    if mem_err:
        st["warnings"].append(f"Meko: {mem_err}")
    return {**st, "llm": router.status(), "weights": SCORE_WEIGHTS, "sectors": [s for s in SECTORS if s != "other"],
            "data_quality": rep, "demo_mode": settings.demo_mode, "languages": I18N_LANGUAGES}


@app.get("/api/top-spenders")
def top_spenders():
    # Public filing totals only (company name + reported spend); used by the login page marquee.
    return {"data_source": B().csr.data_source(), "companies": B().csr.top_spenders(24)}


@app.get("/api/data-quality")
def data_quality():
    return B().csr.data_quality_report()


class RowsReq(BaseModel):
    refs: list[str]


@app.post("/api/rows")
def rows(req: RowsReq, user: dict = Depends(current_user)):
    return B().csr.get_rows(req.refs)  # public filing rows only


@app.get("/api/companies/{company_id}")
def company(company_id: str, user: dict = Depends(current_user)):
    prof = B().csr.company_profile(company_id)
    if not prof:
        raise HTTPException(404, "Unknown company")
    return prof


# -- NGO: pipeline ------------------------------------------------------------------
class RunReq(BaseModel):
    cause: str
    state: str
    district: str = ""
    project_summary: str = ""
    funding_ask_inr: float = Field(gt=0)
    sectors: list[str] = []


@app.post("/api/run")
def run(req: RunReq, user: dict = Depends(NGO)):
    prof = STATE["platform"].ngo_profile(user["ngo_id"])
    ngo = NGOProfile(ngo_id=user["ngo_id"], name=prof["name"], **req.model_dump(exclude={"sectors"}))
    try:
        # Ranking returns in ~2 s; model explanations are filled in by a worker (poll /api/runs/latest).
        return run_pipeline(ngo, B(), STATE["llm"], STATE["app"], req.sectors or None, background=True, user_id=user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/runs/latest")
def latest_run(user: dict = Depends(NGO)):
    r = STATE["app"].latest_run(user["ngo_id"])
    if not r:
        raise HTTPException(404, "No run yet")
    return {**r, "ngo": STATE["app"].get_ngo(user["ngo_id"])}


@app.post("/api/runs/{run_id}/replay")
def replay_run(run_id: str, user: dict = Depends(NGO)):
    """Re-run the stored queries and ranking on the recorded dataset version and compare."""
    run = STATE["app"].get_run(run_id, user["ngo_id"])
    if not run:
        raise HTTPException(404, "Run not found")
    return replay(run, B().csr)


# -- draft translation (drafts only; names, IDs, numbers and dates are protected and checked) -------------
class TranslateReq(BaseModel):
    lang: str
    tone: str = "formal"


@app.post("/api/outreach/{company_id}/translate")
def translate_outreach(company_id: str, req: TranslateReq, user: dict = Depends(NGO)):
    from .translate import translate_draft

    pkg = STATE["app"].get_outreach(user["ngo_id"], company_id)
    if not pkg:
        raise HTTPException(404, "Draft the email first.")
    draft = next((d for d in pkg["drafts"] if d["tone"] == req.tone), None)
    if not draft:
        raise HTTPException(404, "No draft with that tone.")
    ngo = STATE["platform"].ngo_profile(user["ngo_id"])
    names = [ngo["name"], pkg["company_name"]]
    session = STATE["llm"].session(f"tr_{uuid.uuid4().hex[:8]}", user["id"])
    try:
        subject = translate_draft(session, draft["subject"], req.lang, names)
        body = translate_draft(session, draft["body"], req.lang, names)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if "unavailable" in (subject["status"], body["status"]) and session.capacity_reached:
        raise HTTPException(429, "AI capacity reached for today. Try later; your English draft is unchanged.")
    ok = subject["status"] == body["status"] == "translated"
    return {"status": "translated" if ok else ("rejected" if "rejected" in (subject["status"], body["status"]) else "unavailable"),
            "lang": req.lang, "tone": req.tone, "subject": subject["text"], "body": body["text"],
            "label": subject["label"] if ok else "", "checks": [subject.get("check"), body.get("check")],
            "reason": None if ok else (body.get("reason") or subject.get("reason"))}


# -- NGO: email outreach (copy / mailto) ------------------------------------------------
class OutreachReq(BaseModel):
    company_id: str
    regenerate: bool = False


def _ranked(ngo_id: str, company_id: str) -> tuple[RankedFunder, dict]:
    run = STATE["app"].latest_run(ngo_id)
    if not run:
        raise HTTPException(404, "Run the pipeline first")
    for r in run["ranked"]:
        if r["company_id"] == company_id:
            return RankedFunder(**r), run
    raise HTTPException(404, "Company not in the latest ranking")


@app.post("/api/outreach")
def outreach(req: OutreachReq, user: dict = Depends(NGO)):
    ngo_id = user["ngo_id"]
    existing = STATE["app"].get_outreach(ngo_id, req.company_id)
    if existing and not req.regenerate:
        return existing
    ctx = ctx_for(ngo_id, user["id"])
    r, run = _ranked(ngo_id, req.company_id)
    others = [x["company_name"] for x in run["ranked"] if x["company_id"] != req.company_id]
    pkg = Writer().run(ctx, r, run["sectors"], others).model_dump()
    pkg["approved"] = False
    STATE["app"].save_outreach(ngo_id, req.company_id, pkg)
    history = ctx.memory.recall(ngo_id, req.company_id)
    if not any(e.type == "drafted" for e in history):
        ctx.memory.remember(new_event(ngo_id, req.company_id, "drafted", datetime.now(timezone.utc),
                                      company_name=r.company_name, text="Outreach email drafted (formal + warm)."))
    return pkg


@app.get("/api/outreach")
def list_outreach(user: dict = Depends(NGO)):
    return STATE["app"].list_outreach(user["ngo_id"])


class SaveDraftsReq(BaseModel):
    drafts: list[EmailDraft]


@app.put("/api/outreach/{company_id}")
def save_drafts(company_id: str, req: SaveDraftsReq, user: dict = Depends(NGO)):
    pkg = STATE["app"].get_outreach(user["ngo_id"], company_id)
    if not pkg:
        raise HTTPException(404, "No drafts yet")
    pkg["drafts"] = [d.model_dump() for d in req.drafts]
    pkg["edited"] = True
    STATE["app"].save_outreach(user["ngo_id"], company_id, pkg)
    return pkg


class MarkSentReq(BaseModel):
    tone: str
    subject: str
    body: str
    approved: bool = Field(description="Human confirmed they reviewed and sent this email themselves")


@app.post("/api/outreach/{company_id}/sent")
def mark_sent(company_id: str, req: MarkSentReq, user: dict = Depends(NGO)):
    if not req.approved:
        raise HTTPException(400, "A human must approve the email before it is marked as sent.")
    ngo_id = user["ngo_id"]
    pkg = STATE["app"].get_outreach(ngo_id, company_id) or {}
    ctx = ctx_for(ngo_id, user["id"])
    ev = new_event(ngo_id, company_id, "sent", datetime.now(timezone.utc), company_name=pkg.get("company_name", ""),
                   text=f"Subject: {req.subject}\n\n{req.body}", meta={"tone": req.tone})
    FollowUpTracker().record(ctx, ev)
    if pkg:
        pkg["approved"] = True
        pkg["sent_event_id"] = ev.event_id
        STATE["app"].save_outreach(ngo_id, company_id, pkg)
    return ev


# -- NGO: follow-ups (email history from memory + in-app pitch threads from SQLite) ------------
def _company_names(ngo_id: str) -> dict[str, str]:
    run = STATE["app"].latest_run(ngo_id)
    return {r["company_id"]: r["company_name"] for r in (run or {}).get("ranked", [])}


@app.get("/api/followups")
def followups(user: dict = Depends(NGO)):
    ngo_id = user["ngo_id"]
    memory = B().memory
    names = _company_names(ngo_id)
    tracker = FollowUpTracker()
    out = []
    by_company: dict[str, list] = {}
    for e in memory.recall(ngo_id):
        by_company.setdefault(e.company_id, []).append(e)
    for cid, events in by_company.items():
        step = tracker.next_step(events)
        out.append({"key": cid, "company_id": cid, "company_name": names.get(cid) or events[0].company_name,
                    "channel": "email", "events": [e.model_dump() for e in events], "next_step": step.model_dump()})
    out.extend(STATE["platform"].ngo_thread_followups(ngo_id, tracker))
    out.sort(key=lambda x: (not x["next_step"]["overdue"], x["next_step"]["due"] or "9999"))
    return {"memory_backend": memory.backend_name, "companies": out}


class EventReq(BaseModel):
    type: str
    text: str = ""
    at: str | None = None


@app.post("/api/followups/{company_id}/events")
def add_event(company_id: str, req: EventReq, user: dict = Depends(NGO)):
    if req.type not in ("reply_received", "no_reply", "note", "sent"):
        raise HTTPException(400, "type must be reply_received, no_reply, note or sent")
    ngo_id = user["ngo_id"]
    ctx = ctx_for(ngo_id, user["id"])
    at = datetime.fromisoformat(req.at) if req.at else datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    ev = new_event(ngo_id, company_id, req.type, at, company_name=_company_names(ngo_id).get(company_id, ""), text=req.text)
    return [e.model_dump() for e in FollowUpTracker().record(ctx, ev)]


@app.post("/api/followups/{key}/draft")
def draft_next(key: str, user: dict = Depends(NGO)):
    ngo_id = user["ngo_id"]
    ctx = ctx_for(ngo_id, user["id"])
    tracker = FollowUpTracker()
    if key.startswith("pitch:"):
        history = STATE["platform"].ngo_thread_history(ngo_id, key.split(":", 1)[1])  # 404 if not this NGO's
    else:
        history = ctx.memory.recall(ngo_id, key)
    step = tracker.plan_history(ctx, history, draft=True)
    return {"recalled_events": [e.model_dump() for e in history], "next_step": step.model_dump()}


# -- built frontend (optional single-port mode) ---------------------------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        f = DIST / path
        return FileResponse(f if path and f.is_file() else Path(DIST / "index.html"))
