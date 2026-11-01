"""Platform API: auth, NGO profile + in-app pitches, company side, donors.

Rules enforced here:
  - 401 without a valid session, 403 for the wrong role (see auth.require_role).
  - ngo_id / company_id always come from the session, never from the request.
  - No auto-sending or auto-rejecting: every outbound message needs approved=true from a person.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..agents.screener import recommend_ngos, screen
from ..crosscheck import generate_checked, split_sentences
from ..formatting import inr
from ..state import STATE
from ..verify import verify_claims
from .auth import COOKIE, current_user, require_role, require_verified

router = APIRouter(prefix="/api")
NGO = require_role("ngo")
COMPANY = require_role("company")
DONOR = require_role("donor")


def P():
    return STATE["platform"]


def _cookie(resp: Response, value: str) -> None:
    s = P().settings
    resp.set_cookie(COOKIE, value, httponly=True, samesite="lax", secure=s.cookie_secure, max_age=s.session_hours * 3600, path="/")


# -- auth ---------------------------------------------------------------------------------
class SignupReq(BaseModel):
    email: str
    password: str
    role: str = Field(pattern="^(ngo|company|donor)$")
    name: str = Field(min_length=2, max_length=200)
    consent: bool


@router.post("/auth/signup")
def signup(req: SignupReq, resp: Response):
    p = P()
    user = p.auth.create_user(req.email, req.password, req.role, consent=req.consent)
    if req.role == "ngo":
        p.create_ngo(user["id"], req.name)
    elif req.role == "donor":
        p.donors.create(user["id"], req.name)
    else:
        p.create_company(user["id"], req.name)
    if p.settings.require_email_verification:
        p.auth.send_verification(user)
    _cookie(resp, p.auth.start_session(user["id"]))
    msg = ("Account created. Verify your email: in DEMO MODE the link is printed in the server console."
           if p.settings.require_email_verification else "Account created.")
    return {"ok": True, "demo_mode": p.settings.demo_mode, "message": msg}


class LoginReq(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(req: LoginReq, request: Request, resp: Response):
    ip = request.client.host if request.client else "unknown"
    _cookie(resp, P().auth.login(req.email, req.password, ip))
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, resp: Response):
    P().auth.end_session(request.cookies.get(COOKIE))
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class LanguageReq(BaseModel):
    language: str


@router.put("/auth/me/language")
def set_language(req: LanguageReq, user: dict = Depends(current_user)):
    from ..config import I18N_LANGUAGES

    if req.language not in I18N_LANGUAGES:
        raise HTTPException(422, f"Language must be one of {', '.join(I18N_LANGUAGES)}.")
    P().db.run("UPDATE users SET language = ? WHERE id = ?", (req.language, user["id"]))
    return {"language": req.language}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    out = {k: user[k] for k in ("email", "role", "email_verified", "is_demo")}
    row = P().db.one("SELECT language FROM users WHERE id = ?", (user["id"],))
    out["language"] = (row or {}).get("language") or "en"
    if user["role"] == "admin":
        out["profile"] = {"name": user["email"], "scope": P().admin.scope(user)}
    if user["role"] == "ngo":
        out["profile"] = P().ngo_profile(user["ngo_id"])
    elif user["role"] == "company":
        out["profile"] = P().company_profile(user["company_id"])
    elif user["role"] == "donor":
        out["profile"] = P().donors.profile(user["donor_id"])
    return out


class TokenReq(BaseModel):
    token: str


@router.post("/auth/verify")
def verify(req: TokenReq):
    P().auth.verify_email(req.token)
    return {"ok": True}


@router.post("/auth/resend-verification")
def resend(user: dict = Depends(current_user)):
    if not user["email_verified"]:
        P().auth.send_verification({"id": user["id"], "email": user["email"]})
    return {"ok": True}


class ResetRequestReq(BaseModel):
    email: str


@router.post("/auth/request-reset")
def request_reset(req: ResetRequestReq):
    P().auth.request_reset(req.email)
    return {"ok": True, "message": "If that account exists, a reset link was sent (DEMO MODE: server console)."}


class ResetReq(BaseModel):
    token: str
    new_password: str


@router.post("/auth/reset")
def reset(req: ResetReq):
    P().auth.reset_password(req.token, req.new_password)
    return {"ok": True}


# -- NGO ------------------------------------------------------------------------------------
@router.get("/ngo/profile")
def ngo_profile(user: dict = Depends(NGO)):
    ngo = P().ngo_profile(user["ngo_id"])
    return {"profile": ngo, "readiness": P().readiness(ngo), "public_listing_preview": P().public_listing(ngo)}


@router.put("/ngo/profile")
def update_ngo_profile(fields: dict, user: dict = Depends(NGO)):
    ngo = P().update_ngo(user["ngo_id"], fields)
    return {"profile": ngo, "readiness": P().readiness(ngo), "public_listing_preview": P().public_listing(ngo)}


@router.get("/ngo/platform-match/{cin}")
def platform_match(cin: str, user: dict = Depends(NGO)):
    """Is this ranked company on the platform (approved, same CIN)? Otherwise use your own email."""
    return {"match": P().match_company_by_cin(cin)}


class PitchReq(BaseModel):
    company_id: str
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=20, max_length=6000)
    evidence_refs: list[str] = []
    approved: bool = Field(description="The NGO reviewed this pitch and wants it sent")


@router.post("/ngo/pitches")
def send_pitch(req: PitchReq, user: dict = Depends(NGO)):
    if not req.approved:
        raise HTTPException(400, "Review and approve the pitch before it is sent.")
    require_verified(user)
    ngo_id = user["ngo_id"]
    run = STATE["app"].latest_run(ngo_id) or {}
    run_ngo = run.get("ngo") or STATE["app"].get_ngo(ngo_id) or {}
    prof = P().ngo_profile(ngo_id)
    valid_refs = {r["source_ref"] for r in STATE["backends"].csr.get_rows(req.evidence_refs)}  # public rows only
    pitch = P().pitches.create_and_send(
        ngo_id, req.company_id, req.subject, req.body, [r for r in req.evidence_refs if r in valid_refs],
        ask_inr=run_ngo.get("funding_ask_inr"), sectors=run.get("sectors") or prof["causes"],
        state=prof["state"] or run_ngo.get("state", ""), district=prof["district"] or run_ngo.get("district", ""))
    return pitch


@router.get("/ngo/pitches")
def my_pitches(user: dict = Depends(NGO)):
    return P().pitches.list_for_ngo(user["ngo_id"])


@router.get("/ngo/pitches/{pitch_id}")
def my_pitch(pitch_id: str, user: dict = Depends(NGO)):
    p = P().pitches.get_for_ngo(pitch_id, user["ngo_id"])
    return {"pitch": p, "company": {"name": P().company_profile(p["company_id"])["name"],
                                    "is_demo": P().company_profile(p["company_id"])["is_demo"]},
            "events": P().pitches.events(pitch_id, "ngo")}


# -- company ---------------------------------------------------------------------------------
@router.get("/company/profile")
def company_profile(user: dict = Depends(COMPANY)):
    return P().company_profile(user["company_id"])


@router.put("/company/profile")
def update_company_profile(fields: dict, user: dict = Depends(COMPANY)):
    return P().update_company(user["company_id"], fields)


@router.get("/company/footprint")
def footprint(user: dict = Depends(COMPANY)):
    """Your reported CSR footprint in public filings, joined by CIN."""
    comp = P().company_profile(user["company_id"])
    if not comp["cin"]:
        return {"cin": None, "found": False, "message": "Add your CIN to see your reported CSR footprint: unknown."}
    prof = STATE["backends"].csr.company_profile(comp["cin"])
    if not prof:
        return {"cin": comp["cin"], "found": False,
                "message": "No filings with this CIN in the loaded dataset. Your footprint is unknown here."}
    return {"cin": comp["cin"], "found": True, "data_source": STATE["backends"].csr.data_source(), **prof,
            "unknowns": ["Spend not reported against this CIN (e.g. under a parent/subsidiary): unknown",
                         "Current-year CSR budget and approvals: unknown"]}


@router.get("/company/directory")
def directory(sector: str = "", state: str = "", district: str = "", user: dict = Depends(COMPANY)):
    """Listed NGOs, ranked by fit with this company's own profile (focus sectors, focus states)."""
    company = P().company_profile(user["company_id"])
    return recommend_ngos(company, P().directory(sector, state, district, company_id=user["company_id"]))


def _pitch_view(p: dict) -> dict:
    ngo = P().ngo_profile(p["ngo_id"])
    # What the NGO disclosed by pitching: identity, location, causes, self-declared credentials. Never its
    # private memory, funder ranking or other pitches.
    st = P().verification.status("ngo", p["ngo_id"])
    return {"name": ngo["name"], "is_demo": ngo["is_demo"], "state": ngo["state"], "district": ngo["district"],
            "verification": {"level": st["level"], "badge": st["badge"], "warnings": st["warnings"]},
            "causes": ngo["causes"], "credentials": [{"label": i["label"], "value": i["value"], "source": i["source"]}
                                                     for i in P().readiness(ngo) if i["done"]]}


@router.get("/company/inbox")
def inbox(user: dict = Depends(COMPANY)):
    return P().pitches.list_for_company(user["company_id"])


@router.get("/company/pitches/{pitch_id}")
def company_pitch(pitch_id: str, user: dict = Depends(COMPANY)):
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    P().pitches.mark_viewed(p)
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    events = P().pitches.events(pitch_id, "company")
    screened = [e for e in events if e["type"] == "screened"]
    screening = dict(screened[-1]["payload"]) if screened else None
    if screening and not isinstance(screening.get("summary"), list):  # saved before the headline fix
        screening["summary"] = []
    return {"pitch": p, "ngo": _pitch_view(p), "events": events, "screening": screening}


@router.post("/company/pitches/{pitch_id}/screen")
def screen_pitch(pitch_id: str, user: dict = Depends(COMPANY)):
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    session = STATE["llm"].session(f"screen_{uuid.uuid4().hex[:8]}")
    result = screen(session, p, P().ngo_profile(p["ngo_id"]), P().company_profile(user["company_id"]))
    P().pitches.add_event(p, "screened", "system", result | {"headline": f"Fit {result['fit']['total']}/100 (advisory)"})
    return result


class DraftReplyReq(BaseModel):
    action: str = Field(pattern="^(interested|info_requested|declined)$")


class ReplyOut(BaseModel):
    reply: str


REPLY_SYSTEM = """You draft a short, courteous reply from a company's CSR team to an NGO's pitch.
The company's decision is given; do not change it. Use only facts from the pitch and company profile.
Never invent amounts, dates, names or commitments. Under 120 words. Address the NGO by its name."""


@router.post("/company/pitches/{pitch_id}/draft-reply")
def draft_reply(pitch_id: str, req: DraftReplyReq, user: dict = Depends(COMPANY)):
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    comp, ngo = P().company_profile(user["company_id"]), P().ngo_profile(p["ngo_id"])
    decision = {"interested": "We are interested and would like to take this forward.",
                "info_requested": "We need more information before deciding.",
                "declined": "This is not a fit for our current CSR priorities."}[req.action]
    evidence = (f"Company: {comp['name']}\nDecision: {decision}\nNGO: {ngo['name']}\nPitch subject: {p['subject']}\n"
                f"Pitch: {p['body']}\nAsk: {inr(p['ask_inr'])}")
    template = (f"Dear {ngo['name']} team,\n\nThank you for your proposal \"{p['subject']}\". {decision}\n\n"
                f"Regards,\nCSR Team, {comp['name']}")
    session = STATE["llm"].session(f"reply_{uuid.uuid4().hex[:8]}")
    checked = generate_checked(
        session, "reply_draft", REPLY_SYSTEM, evidence, ReplyOut,
        lambda o: verify_claims([o.reply], evidence, allowed_companies=[comp["name"], ngo["name"]]),
        lambda o: split_sentences(o.reply), evidence) if session.enabled else None
    if checked and checked.output is not None:
        return {"draft": checked.output.reply, "status": checked.status, "produced_by": checked.produced_by,
                "fallback_from": checked.fallback_from, "review_problems": []}
    return {"draft": template, "status": checked.status if checked else "template", "produced_by": None,
            "fallback_from": checked.fallback_from if checked else None,
            "review_problems": checked.problems if checked else [], "rejected": checked.rejected if checked else None}


class Promise(BaseModel):
    what: str = ""
    amount: float | None = None
    due_date: str | None = None


class RespondReq(BaseModel):
    action: str = Field(pattern="^(interested|info_requested|declined)$")
    reply_text: str = ""
    promise: Promise | None = None
    approved: bool = Field(description="A person at the company reviewed this reply")


@router.post("/company/pitches/{pitch_id}/respond")
def respond(pitch_id: str, req: RespondReq, user: dict = Depends(COMPANY)):
    if not req.approved:
        raise HTTPException(400, "A person must approve the reply before it is sent.")
    require_verified(user)
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    return P().pitches.respond(p, req.action, req.reply_text, req.promise.model_dump() if req.promise else None)


class ReportReq(BaseModel):
    reason: str = ""


@router.post("/company/pitches/{pitch_id}/report")
def report(pitch_id: str, req: ReportReq, user: dict = Depends(COMPANY)):
    p = P().pitches.get_for_company(pitch_id, user["company_id"])
    P().pitches.report_and_block(p, req.reason)
    return {"ok": True, "message": "Reported as unsolicited. This NGO can no longer pitch you and is hidden from your directory."}


@router.get("/company/pipeline")
def pipeline(user: dict = Depends(COMPANY)):
    return P().pitches.pipeline(user["company_id"])


# -- verification: own status --------------------------------------------------------------
def _own_entity(user: dict) -> tuple[str, str]:
    if user["role"] == "ngo":
        return "ngo", user["ngo_id"]
    if user["role"] == "company":
        return "company", user["company_id"]
    raise HTTPException(403, "Only NGOs and companies have a verification status.")


@router.get("/verification/me")
def my_verification(user: dict = Depends(require_role("ngo", "company"))):
    t, eid = _own_entity(user)
    st = P().verification.status(t, eid)
    return {k: st[k] for k in ("level", "badge", "warnings", "auto_checks")}


# -- NGO: individual donors (updates, supporters) ------------------------------------------------
class UpdateDraftReq(BaseModel):
    facts: str = Field(min_length=10, max_length=3000)


class UpdateOut(BaseModel):
    title: str
    body: str


UPDATE_SYSTEM = """You help a small Indian NGO turn its own notes into a short update for supporters.
Use ONLY the facts provided by the NGO. Do not add numbers, outcomes, names, dates or claims that are not in
the notes. No impact figures unless they appear verbatim in the notes. Title under 10 words, body under 120 words."""


@router.post("/ngo/updates/draft")
def draft_update(req: UpdateDraftReq, user: dict = Depends(NGO)):
    ngo = P().ngo_profile(user["ngo_id"])
    evidence = f"NGO: {ngo['name']}\nNotes from the NGO:\n{req.facts}"
    session = STATE["llm"].session(f"upd_{uuid.uuid4().hex[:8]}")
    checked = generate_checked(
        session, "update_draft", UPDATE_SYSTEM, evidence, UpdateOut,
        lambda o: verify_claims([o.title, o.body], evidence, allowed_companies=[ngo["name"]]),
        lambda o: split_sentences(o.title) + split_sentences(o.body), evidence) if session.enabled else None
    if checked and checked.output is not None:
        return {"title": checked.output.title, "body": checked.output.body, "status": checked.status,
                "produced_by": checked.produced_by}
    return {"title": f"Update from {ngo['name']}", "body": req.facts.strip(), "status": checked.status if checked else "template",
            "produced_by": None, "review_problems": checked.problems if checked else []}


class PostUpdateReq(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=5000)
    approved: bool


@router.post("/ngo/updates")
def post_update(req: PostUpdateReq, user: dict = Depends(NGO)):
    if not req.approved:
        raise HTTPException(400, "Review and approve the update before posting.")
    return P().donors.post_update(user["ngo_id"], req.title, req.body)


@router.get("/ngo/updates")
def my_updates(user: dict = Depends(NGO)):
    return P().donors.updates(user["ngo_id"])


@router.get("/ngo/supporters")
def supporters(user: dict = Depends(NGO)):
    return P().donors.supporters(user["ngo_id"])


@router.post("/ngo/gifts/{gift_id}/confirm")
def confirm_gift(gift_id: str, user: dict = Depends(NGO)):
    P().donors.confirm_gift(user["ngo_id"], gift_id)
    return {"ok": True}


# -- donor ----------------------------------------------------------------------------------------
@router.get("/donor/profile")
def donor_profile(user: dict = Depends(DONOR)):
    return P().donors.profile(user["donor_id"])


@router.put("/donor/profile")
def update_donor_profile(fields: dict, user: dict = Depends(DONOR)):
    return P().donors.update(user["donor_id"], fields)


@router.get("/donor/discover")
def discover(cause: str = "", state: str = "", user: dict = Depends(DONOR)):
    session = STATE["llm"].session(f"match_{uuid.uuid4().hex[:8]}")
    return P().donors.discover(user["donor_id"], session, cause, state)


@router.get("/donor/memory")
def donor_memory(user: dict = Depends(DONOR)):
    """What FundBridge remembers about you (your private conversation only)."""
    return [m for m in P().donors.recall(user["donor_id"]) if m.get("type") != "matcher_trace"]


@router.get("/donor/lists")
def donor_lists(user: dict = Depends(DONOR)):
    return P().donors.lists(user["donor_id"])


class ToggleReq(BaseModel):
    on: bool = True
    share_name: bool | None = None


@router.post("/donor/shortlist/{ngo_id}")
def shortlist(ngo_id: str, req: ToggleReq, user: dict = Depends(DONOR)):
    P().donors.shortlist(user["donor_id"], ngo_id, req.on)
    return P().donors.lists(user["donor_id"])


@router.post("/donor/follow/{ngo_id}")
def follow(ngo_id: str, req: ToggleReq, user: dict = Depends(DONOR)):
    P().donors.follow(user["donor_id"], ngo_id, req.on, req.share_name)
    return P().donors.lists(user["donor_id"])


@router.get("/donor/feed")
def feed(user: dict = Depends(DONOR)):
    return P().donors.feed(user["donor_id"])


@router.get("/donor/give/{ngo_id}")
def give(ngo_id: str, user: dict = Depends(DONOR)):
    """Returns the NGO's own donation page and a notice. FundBridge never calls a payment API."""
    return P().donors.give(ngo_id)


class GiftReq(BaseModel):
    ngo_id: str
    amount_inr: float = Field(gt=0)
    date: str
    share_name: bool | None = None


@router.post("/donor/gifts")
def report_gift(req: GiftReq, user: dict = Depends(DONOR)):
    return P().donors.report_gift(user["donor_id"], req.ngo_id, req.amount_inr, req.date, req.share_name)


@router.get("/donor/gifts")
def my_gifts(user: dict = Depends(DONOR)):
    return P().donors.gifts(user["donor_id"])


class ReportNgoReq(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/donor/report/{ngo_id}")
def report_ngo(ngo_id: str, req: ReportNgoReq, user: dict = Depends(DONOR)):
    P().donors._require_discoverable(ngo_id)
    P().donors.report_ngo("donor", user["donor_id"], ngo_id, req.reason)
    return {"ok": True, "message": "Thank you. This NGO is now hidden from your recommendations."}
