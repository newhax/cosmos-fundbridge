"""Two-sided platform: role guards, isolation, directory, screener invariance, thread persistence, limits."""
import dataclasses

import pytest
from fastapi.testclient import TestClient
from platform_fixtures import HARA, MARUDHAR, PW, SAHYADRI, SAKHI, SHIKSHA, THAR, install, login

from fundbridge.agents.screener import fit_score, screen
from fundbridge.api import app
from fundbridge.config import DATASETS_DIR
from fundbridge.state import STATE

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")


@pytest.fixture()
def env(tmp_path):
    with TestClient(app):  # runs the lifespan once (backends), then we swap in isolated stores
        yield install(tmp_path), tmp_path


def company_id(platform, email):
    return platform.db.one("SELECT c.company_id FROM company_profiles c JOIN users u ON u.id = c.user_id "
                           "WHERE u.email = ?", (email,))["company_id"]


def seeded_pitch(platform):
    return platform.db.one("SELECT * FROM pitches WHERE ngo_id = 'shiksha-setu-demo'")


# -- role guards ----------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", [
    ("get", "/api/ngo/pitches"), ("get", "/api/followups"), ("get", "/api/runs/latest"), ("get", "/api/company/inbox"),
    ("post", "/api/rows"), ("get", "/api/auth/me"),
])
def test_unauthenticated_gets_401(env, method, path):
    c = TestClient(app)
    r = getattr(c, method)(path, **({"json": {"refs": []}} if method == "post" else {}))
    assert r.status_code == 401


def test_wrong_role_gets_403(env):
    ngo, comp = login(SHIKSHA), login(THAR)
    assert ngo.get("/api/company/inbox").status_code == 403
    assert ngo.get("/api/company/directory").status_code == 403
    assert comp.get("/api/ngo/pitches").status_code == 403
    assert comp.get("/api/followups").status_code == 403
    assert comp.get("/api/runs/latest").status_code == 403
    assert comp.post("/api/run", json={"cause": "x", "state": "Rajasthan", "funding_ask_inr": 1}).status_code == 403


# -- isolation ------------------------------------------------------------------------------------
def test_ngo_cannot_read_another_ngos_pitches_events_or_memory(env):
    platform, _ = env
    pid = seeded_pitch(platform)["id"]
    a, b = login(SHIKSHA), login(HARA)
    assert a.get(f"/api/ngo/pitches/{pid}").status_code == 200
    assert b.get(f"/api/ngo/pitches/{pid}").status_code == 404           # not even existence is revealed
    assert b.get("/api/ngo/pitches").json() == []
    assert b.post(f"/api/followups/pitch:{pid}/draft").status_code in (404,)  # B has no run -> 404 either way
    # A's private email-outreach memory is invisible to B
    from datetime import datetime, timezone

    from fundbridge.agents.followup import new_event
    STATE["backends"].memory.remember(new_event("shiksha-setu-demo", "SYN0000000000002", "sent",
                                                datetime.now(timezone.utc), company_name="Aravali"))
    b_follow = b.get("/api/followups").json()["companies"]
    assert b_follow == []
    a_keys = {x["key"] for x in a.get("/api/followups").json()["companies"]}
    assert f"pitch:{pid}" in a_keys and "SYN0000000000002" in a_keys


def test_client_supplied_ids_are_ignored(env):
    platform, _ = env
    b = login(HARA)
    # B tries to read A's run / followups by passing A's id: the server uses B's session id instead
    assert b.get("/api/runs/latest", params={"ngo_id": "shiksha-setu-demo"}).status_code == 404
    assert b.get("/api/followups", params={"ngo_id": "shiksha-setu-demo"}).json()["companies"] == []


def test_company_sees_only_its_pitches_and_never_ngo_memory(env):
    platform, _ = env
    pid = seeded_pitch(platform)["id"]
    thar, marudhar = login(THAR), login(MARUDHAR)
    view = thar.get(f"/api/company/pitches/{pid}").json()
    assert view["pitch"]["id"] == pid
    assert set(view) == {"pitch", "ngo", "events", "screening"}  # no ranking, traces or other pitches
    assert all(e["visibility"] in ("shared", "company") for e in view["events"])
    assert marudhar.get(f"/api/company/pitches/{pid}").status_code == 404
    assert marudhar.get("/api/company/inbox").json() == []
    assert thar.get("/api/followups").status_code == 403
    assert thar.post(f"/api/followups/pitch:{pid}/draft").status_code == 403
    # NGO-private events (nudges) are not shown to the company
    platform.pitches.add_event(platform.pitches.get_for_company(pid, company_id(platform, THAR)), "nudge_suggested",
                               "system", {"action": "nudge"})
    types = [e["type"] for e in thar.get(f"/api/company/pitches/{pid}").json()["events"]]
    assert "nudge_suggested" not in types and "drafted" not in types


# -- directory -------------------------------------------------------------------------------------
def test_directory_hides_unlisted_ngos_and_unlisted_fields(env):
    platform, _ = env
    thar = login(THAR)
    names = [n["name"] for n in thar.get("/api/company/directory").json()]
    assert "Sakhi Collective (DEMO)" not in names and "Shiksha Setu Foundation (DEMO)" in names
    hara = next(n for n in thar.get("/api/company/directory").json() if n["name"].startswith("Hara"))
    assert "credentials" not in hara  # Hara did not list credentials
    shiksha = next(n for n in thar.get("/api/company/directory").json() if n["name"].startswith("Shiksha"))
    assert all(c["source"] == "self-declared" for c in shiksha["credentials"])
    assert "verified" not in str(shiksha).lower()  # nobody checks records by hand, so never "verified"
    mh = thar.get("/api/company/directory", params={"state": "Maharashtra"}).json()
    assert mh and all(n["state"] == "Maharashtra" for n in mh) and "Sakhi Collective (DEMO)" not in [n["name"] for n in mh]
    sakhi = login(SAKHI)
    sakhi.put("/api/ngo/profile", json={"listed_publicly": True})
    assert "Sakhi Collective (DEMO)" in [n["name"] for n in thar.get("/api/company/directory").json()]


# -- screener ---------------------------------------------------------------------------------------
def test_screener_fit_ignores_wording_quality(env):
    platform, _ = env
    company = platform.company_profile(company_id(platform, THAR))
    ngo = platform.ngo_profile("shiksha-setu-demo")
    base = {"sectors": ["education"], "state": "Rajasthan", "district": "Barmer", "ask_inr": 1_500_000, "subject": "s"}
    polished = base | {"body": "Dear CSR Team, we respectfully propose a carefully designed reading programme for "
                               "twelve schools, with clear milestones, budgets and outcomes. " * 5}
    sloppy = base | {"body": "pls fund us we do libary for kids in barmer need money thx"}
    a, b = screen(None, polished, ngo, company), screen(None, sloppy, ngo, company)
    assert a["fit"] == b["fit"]
    assert a["fit"]["total"] == fit_score({"sectors": ["education"], "state": "Rajasthan", "ask_inr": 1_500_000}, company)["total"]
    # facts do matter
    c = screen(None, sloppy | {"state": "Kerala"}, ngo, company)
    assert c["fit"]["total"] < a["fit"]["total"]


def test_screening_is_advisory_and_company_private(env):
    platform, _ = env
    pid = seeded_pitch(platform)["id"]
    thar = login(THAR)
    before = thar.get(f"/api/company/pitches/{pid}").json()["pitch"]["status"]
    res = thar.post(f"/api/company/pitches/{pid}/screen").json()
    assert len(res["summary"]) == 3 and "fit" in res and res["missing_documents"] == ["80G registration"]
    reopened = thar.get(f"/api/company/pitches/{pid}").json()
    assert reopened["pitch"]["status"] == before  # never auto-decides
    assert reopened["screening"]["summary"] == res["summary"]  # stored result keeps its 3-line summary
    ngo_types = [e["type"] for e in login(SHIKSHA).get(f"/api/ngo/pitches/{pid}").json()["events"]]
    assert "screened" not in ngo_types


# -- shared thread -> NGO timeline, survives restart ---------------------------------------------------
def test_company_reply_reaches_ngo_timeline_and_survives_restart(env):
    platform, tmp_path = env
    ngo = login(SHIKSHA)
    sahyadri_id = company_id(platform, SAHYADRI)
    pitch = ngo.post("/api/ngo/pitches", json={"company_id": sahyadri_id, "subject": "Reading circles",
                                               "body": "Dear CSR Team, we run reading circles in Barmer schools.",
                                               "approved": True}).json()
    comp = login(SAHYADRI)
    assert comp.post(f"/api/company/pitches/{pitch['id']}/respond", json={
        "action": "info_requested", "reply_text": "Please share your audited accounts.", "approved": True,
        "promise": {"what": "Review within two weeks of receiving accounts", "amount": 500000, "due_date": "2026-12-15"},
    }).status_code == 200

    def thread():
        rows = login(SHIKSHA).get("/api/followups").json()["companies"]
        return next(x for x in rows if x["key"] == f"pitch:{pitch['id']}")

    t = thread()
    assert [e["type"] for e in t["events"]] == ["drafted", "sent", "info_requested", "reply_received", "promise_made"]
    assert t["events"][-1]["promise_by"] == "2026-12-15" and t["next_step"]["action"] == "wait"
    shared = STATE["backends"].memory.read_thread("pitch", pitch["id"])
    assert [m["type"] for m in shared] == ["sent", "info_requested", "reply", "promise_made"]  # Meko/local copy

    install(tmp_path, seed=False)  # simulated restart: fresh singletons on the same files
    t2 = thread()
    assert [e["event_id"] for e in t2["events"]] == [e["event_id"] for e in t["events"]]


def test_seeded_thread_has_promise_and_due_date(env):
    platform, _ = env
    pid = seeded_pitch(platform)["id"]
    events = login(SHIKSHA).get(f"/api/ngo/pitches/{pid}").json()["events"]
    promise = next(e for e in events if e["type"] == "promise_made")["payload"]
    assert promise["amount_inr"] == 1_000_000 and promise["due_date"]


# -- limits, approval, auth flows -----------------------------------------------------------------------
def test_login_rate_limit(env):
    c = TestClient(app)
    for _ in range(3):
        assert c.post("/api/auth/login", json={"email": HARA, "password": "wrong-password"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": HARA, "password": PW}).status_code == 429


def test_pitch_daily_limit_and_human_approval(env):
    platform, _ = env
    ngo = login(SHIKSHA)
    body = {"subject": "Hello there", "body": "Dear CSR Team, a short pitch about our reading programme."}
    targets = [company_id(platform, e) for e in (MARUDHAR, SAHYADRI, THAR)]
    assert ngo.post("/api/ngo/pitches", json=body | {"company_id": targets[0], "approved": False}).status_code == 400
    assert ngo.post("/api/ngo/pitches", json=body | {"company_id": targets[0], "approved": True}).status_code == 200
    assert ngo.post("/api/ngo/pitches", json=body | {"company_id": targets[1], "approved": True}).status_code == 200
    assert ngo.post("/api/ngo/pitches", json=body | {"company_id": targets[2], "approved": True}).status_code == 429


def test_report_blocks_further_pitches(env):
    platform, _ = env
    pid = seeded_pitch(platform)["id"]
    assert login(THAR).post(f"/api/company/pitches/{pid}/report", json={"reason": "unsolicited"}).status_code == 200
    r = login(SHIKSHA).post("/api/ngo/pitches", json={"company_id": company_id(platform, THAR), "subject": "Again",
                                                      "body": "Dear CSR Team, following up on our earlier pitch.",
                                                      "approved": True})
    assert r.status_code == 403


def test_signup_without_verification_can_pitch_at_once(env):
    platform, _ = env
    assert platform.settings.require_email_verification is False  # prototype default
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "quick@new.example", "password": "longpassword", "role": "ngo",
                                         "name": "Quick NGO", "consent": True})
    assert r.status_code == 200 and "Verify" not in r.json()["message"]
    assert c.get("/api/auth/me").json()["email_verified"] is True
    pitch = {"company_id": company_id(platform, THAR), "subject": "Hello there",
             "body": "Dear CSR Team, a short pitch from us.", "approved": True}
    assert c.post("/api/ngo/pitches", json=pitch).status_code == 200


def test_signup_verify_reset_and_unverified_cannot_send(env):
    platform, _ = env
    platform.settings = platform.auth.settings = dataclasses.replace(platform.settings, require_email_verification=True)
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "ngo@new.example", "password": "longpassword", "role": "ngo",
                                         "name": "New NGO", "consent": False})
    assert r.status_code == 422  # consent required
    assert c.post("/api/auth/signup", json={"email": "ngo@new.example", "password": "longpassword", "role": "ngo",
                                            "name": "New NGO", "consent": True}).status_code == 200
    thar = company_id(platform, THAR)
    pitch = {"company_id": thar, "subject": "Hello there", "body": "Dear CSR Team, a short pitch from us.", "approved": True}
    assert c.post("/api/ngo/pitches", json=pitch).status_code == 403  # email not verified
    token = platform.auth.outbox[-1]["link"].split("verify=")[1]
    assert c.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert c.post("/api/ngo/pitches", json=pitch).status_code == 200
    assert c.post("/api/auth/verify", json={"token": token}).status_code == 400  # single use
    c.post("/api/auth/request-reset", json={"email": "ngo@new.example"})
    reset = platform.auth.outbox[-1]["link"].split("reset=")[1]
    assert c.post("/api/auth/reset", json={"token": reset, "new_password": "brand-new-pass"}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401  # reset logs out everywhere
    assert login("ngo@new.example", "brand-new-pass").get("/api/auth/me").json()["role"] == "ngo"


def test_credentials_never_labelled_verified(env):
    platform, _ = env
    me = login(SHIKSHA).get("/api/ngo/profile").json()
    assert {i["source"] for i in me["readiness"] if i["done"]} == {"self-declared"}
    platform.update_ngo("shiksha-setu-demo", {"has_80g": True})
    labels = {i["key"]: i["source"] for i in login(SHIKSHA).get("/api/ngo/profile").json()["readiness"]}
    assert labels["has_80g"] == "self-declared" and "verified" not in labels.values()


def test_other_causes_typed_by_ngo_and_company(env):
    ngo, comp = login(SHIKSHA), login(THAR)
    r = ngo.put("/api/ngo/profile", json={"causes": ["education", "  Animal   Welfare ", "other", "education"]})
    assert r.status_code == 200, r.text
    assert ngo.get("/api/ngo/profile").json()["profile"]["causes"] == ["education", "animal welfare"]  # cleaned, kept after reload
    r = comp.put("/api/company/profile", json={"focus_sectors": ["health", "Animal welfare", "mental health"]})
    assert r.status_code == 200 and r.json()["focus_sectors"] == ["health", "animal welfare", "mental health"]

    for bad in (["<b>x</b>"], ["a"], [f"cause {i}" for i in range(6)]):
        r = ngo.put("/api/ngo/profile", json={"causes": bad})
        assert r.status_code == 422, bad
    assert ngo.get("/api/ngo/profile").json()["profile"]["causes"] == ["education", "animal welfare"]  # unchanged on error

    # The Screener matches typed causes by exact wording only (never mapped to a standard sector).
    company = {"focus_sectors": ["health", "animal welfare"], "focus_states": [], "min_ticket": None, "max_ticket": None}
    assert fit_score({"sectors": ["animal welfare"], "state": "", "ask_inr": None}, company)["components"]["sector_fit"] == 1.0
    assert fit_score({"sectors": ["environment"], "state": "", "ask_inr": None}, company)["components"]["sector_fit"] == 0.0


def test_company_directory_is_ranked_by_the_company_profile(env):
    thar = login(THAR)  # seeded focus: education, health, rural development in Rajasthan, Gujarat
    ranked = thar.get("/api/company/directory").json()
    assert len(ranked) >= 10  # the extra listed DEMO NGOs are seeded
    scores = [n["fit"]["total"] for n in ranked]
    assert scores == sorted(scores, reverse=True)
    top = ranked[0]
    assert top["state"] in ("Rajasthan", "Gujarat") and set(top["causes"]) & {"education", "health", "rural development"}
    assert any("(your focus state)" in r for r in top["fit"]["reasons"])
    assert "verified" not in str(top["fit"]).lower()

    # Change the profile and the ranking follows it.
    thar.put("/api/company/profile", json={"focus_sectors": ["disaster relief"], "focus_states": ["Odisha"]})
    top = thar.get("/api/company/directory").json()[0]
    assert top["name"] == "Aapda Raksha Network (DEMO)" and top["fit"]["components"]["cause_fit"] == 0.5
    assert top["fit"]["components"]["state_fit"] == 1.0

    # No focus set: every criterion is unknown (neutral), not a penalty.
    thar.put("/api/company/profile", json={"focus_sectors": [], "focus_states": []})
    fits = [n["fit"] for n in thar.get("/api/company/directory").json()]
    assert all(f["components"]["cause_fit"] == 0.5 and f["components"]["state_fit"] == 0.5 for f in fits)
    assert all("Your focus sectors are not set" in f["unknowns"] for f in fits)
