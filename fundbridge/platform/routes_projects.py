"""Projects, calls (RFP), requests for funds, acceptance, project management, notifications, feedback.

Rules: ngo_id / company_id always come from the session. A company only sees requests sent to it;
an NGO only its own projects and requests. Nothing is sent automatically and no money moves here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..state import STATE
from .auth import COOKIE, current_user, require_role

router = APIRouter(prefix="/api")
NGO = require_role("ngo")
COMPANY = require_role("company")
ANY = Depends(current_user)


def S():
    return STATE["platform"].projects


# -- NGO: projects ------------------------------------------------------------------------------------------
@router.get("/ngo/projects")
def my_projects(user: dict = Depends(NGO)):
    return S().ngo_projects(user["ngo_id"])


@router.post("/ngo/projects")
def create_project(fields: dict, user: dict = Depends(NGO)):
    return S().save_project(user["ngo_id"], fields)


@router.put("/ngo/projects/{project_id}")
def update_project(project_id: str, fields: dict, user: dict = Depends(NGO)):
    return S().save_project(user["ngo_id"], fields, project_id)


class RequestFundsReq(BaseModel):
    company_id: str
    amount_inr: float | None = None
    message: str = ""


@router.post("/ngo/projects/{project_id}/request-funds")
def request_funds(project_id: str, req: RequestFundsReq, user: dict = Depends(NGO)):
    return S().request_funds(user["ngo_id"], project_id, req.company_id, req.amount_inr, req.message)


@router.get("/ngo/companies")
def companies_on_platform(user: dict = Depends(NGO)):
    """Companies an NGO can send a request for funds to (public profile fields only)."""
    p = STATE["platform"]
    out = []
    for r in p.db.all("SELECT company_id FROM company_profiles ORDER BY name"):
        c = p.company_profile(r["company_id"])
        out.append({k: c[k] for k in ("company_id", "name", "is_demo", "focus_sectors", "focus_areas", "min_ticket", "max_ticket")})
    return out


@router.get("/ngo/calls")
def open_calls(page: int = 1, user: dict = Depends(NGO)):
    return S().open_calls(user["ngo_id"], page)


class ProposalReq(BaseModel):
    project_id: str
    amount_inr: float | None = None
    message: str = ""


@router.post("/ngo/calls/{call_id}/proposals")
def submit_proposal(call_id: str, req: ProposalReq, user: dict = Depends(NGO)):
    return S().submit_proposal(user["ngo_id"], call_id, req.project_id, req.amount_inr, req.message)


@router.get("/ngo/requests")
def ngo_requests(user: dict = Depends(NGO)):
    return S().requests_for("ngo", user["ngo_id"])


@router.post("/ngo/requests/{rid}/withdraw")
def withdraw(rid: str, user: dict = Depends(NGO)):
    return S().withdraw(user["ngo_id"], rid)


# -- company: browse projects, calls, decide ----------------------------------------------------------------
@router.get("/company/projects")
def browse_projects(sector: str = "", state: str = "", page: int = 1, user: dict = Depends(COMPANY)):
    return S().browse_projects(user["company_id"], sector, state, page)


@router.get("/company/calls")
def my_calls(user: dict = Depends(COMPANY)):
    return S().company_calls(user["company_id"])


@router.post("/company/calls")
def create_call(fields: dict, user: dict = Depends(COMPANY)):
    return S().create_call(user["company_id"], fields)


@router.post("/company/calls/{call_id}/close")
def close_call(call_id: str, user: dict = Depends(COMPANY)):
    return S().close_call(user["company_id"], call_id)


@router.get("/company/requests")
def company_requests(status: str = "", user: dict = Depends(COMPANY)):
    return S().requests_for("company", user["company_id"], status)


class DecideReq(BaseModel):
    accept: bool
    note: str = ""


@router.post("/company/requests/{rid}/decide")
def decide(rid: str, req: DecideReq, user: dict = Depends(COMPANY)):
    return S().decide(user["company_id"], rid, req.accept, req.note)


# -- both: project management ---------------------------------------------------------------------------
def _side(user: dict) -> tuple[str, str]:
    from fastapi import HTTPException

    if user["role"] == "ngo":
        return "ngo", user["ngo_id"]
    if user["role"] == "company":
        return "company", user["company_id"]
    raise HTTPException(403, "Only NGOs and companies manage projects.")


@router.get("/engagements")
def engagements(user: dict = Depends(require_role("ngo", "company"))):
    return S().engagements(*_side(user))


@router.post("/engagements/{rid}/updates")
def add_update(rid: str, fields: dict, user: dict = Depends(require_role("ngo", "company"))):
    role, eid = _side(user)
    return S().add_update(rid, role, eid, fields)


class DoneReq(BaseModel):
    done: bool = True


@router.post("/engagements/{rid}/milestones/{update_id}")
def milestone_done(rid: str, update_id: str, req: DoneReq, user: dict = Depends(NGO)):
    return S().set_milestone_done(rid, update_id, user["ngo_id"], req.done)


@router.post("/engagements/{rid}/complete")
def complete(rid: str, user: dict = Depends(COMPANY)):
    return S().complete(rid, user["company_id"])


# -- notifications + feedback ---------------------------------------------------------------------------
@router.get("/notifications")
def notifications(user: dict = ANY):
    return S().notifications(user["id"])


@router.post("/notifications/read")
def notifications_read(user: dict = ANY):
    S().mark_read(user["id"])
    return {"ok": True}


@router.post("/feedback")
def feedback(fields: dict, request: Request):
    """Open to everyone, logged in or not (the user is attached when there is a session)."""
    user = STATE["platform"].auth.user_for_cookie(request.cookies.get(COOKIE))
    return S().add_feedback(user, fields)
