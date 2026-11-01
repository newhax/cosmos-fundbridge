"""Projects/calls/requests, multi-region matching, coverage + fallback, admin scoping (spec tests 1, 4, 5, 8)."""
import pytest
from fastapi.testclient import TestClient
from platform_fixtures import HARA, MARUDHAR, SHIKSHA, THAR, install, login

from fundbridge.api import app
from fundbridge.config import DATASETS_DIR, GEO_MATCH_WEIGHTS
from fundbridge.platform.areas import area_match
from fundbridge.state import STATE

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")


@pytest.fixture()
def env(tmp_path):
    with TestClient(app):
        yield install(tmp_path)


def code(platform, name):
    return platform.geo.resolve_state(name).code


def district(platform, state, name):
    return platform.geo.resolve_district(name, code(platform, state)).code


def ngo_id(platform, email):
    return platform.db.one("SELECT n.ngo_id FROM ngo_profiles n JOIN users u ON u.id = n.user_id WHERE u.email = ?",
                           (email,))["ngo_id"]


def project(platform, title, areas, sectors=("education",), budget=1_500_000):
    return {"title": title, "sectors": list(sectors), "summary": f"{title}: reading circles in village schools.",
            "budget_inr": budget, "duration_months": 12, "areas": areas}


# -- 1. every state/UT has a coverage row --------------------------------------------------------------------
def test_coverage_lists_every_active_state_and_ut_from_the_lgd_file(env):
    c = TestClient(app).get("/api/coverage").json()  # public, no login
    loaded = {s["lgd_code"] for s in env.geo.states()}
    assert {s["state_lgd"] for s in c["states"]} == loaded
    assert all(s["level"] in ("GOOD", "THIN", "NONE") for s in c["states"])
    assert "absent data does not mean no CSR activity" in c["note"]
    some = next(s for s in c["states"] if s["n_districts"])
    d = TestClient(app).get(f"/api/coverage/{some['state_lgd']}").json()
    assert len(d["districts"]) == some["n_districts"] and "districts_with_no_reported_projects" in d
    states = TestClient(app).get("/api/geo/states").json()["states"]
    assert len(states) == len(loaded) and {s["kind"] for s in states} <= {"state", "ut"}


# -- 4. matching levels -------------------------------------------------------------------------------------
def test_area_match_prefers_district_then_state_then_pan_india(env):
    p = env
    raj, barmer = code(p, "Rajasthan"), district(p, "Rajasthan", "Barmer")
    jodhpur = district(p, "Rajasthan", "Jodhpur")
    dist = {"level": "district", "state": raj, "district": barmer}
    assert area_match([dist], [dist])["score"] == GEO_MATCH_WEIGHTS["district"]
    assert area_match([dist], [{"level": "district", "state": raj, "district": jodhpur}])["score"] == GEO_MATCH_WEIGHTS["state"]
    assert area_match([dist], [{"level": "all_india", "state": "", "district": ""}])["score"] == GEO_MATCH_WEIGHTS["all_india"]
    assert area_match([dist], [{"level": "state", "state": code(p, "Kerala"), "district": ""}])["score"] == 0.0
    unknown = area_match([], [dist])
    assert unknown["score"] is None and unknown["level"] == "unknown"  # missing location: unknown, not a guess
    assert GEO_MATCH_WEIGHTS["district"] > GEO_MATCH_WEIGHTS["state"] > GEO_MATCH_WEIGHTS["all_india"]


def test_company_project_ranking_follows_area_levels(env):
    p = env
    raj, barmer = code(p, "Rajasthan"), district(p, "Rajasthan", "Barmer")
    comp = login(THAR)
    comp.put("/api/company/profile", json={"focus_sectors": ["education"], "min_ticket": 500_000, "max_ticket": 5_000_000,
                                           "focus_areas": [{"level": "district", "state": raj, "district": barmer}]})
    ngo = login(SHIKSHA)
    for title, areas in [("District project", [{"level": "district", "state": raj, "district": barmer}]),
                         ("Statewide project", [{"level": "state", "state": raj}]),
                         ("Pan India project", [{"level": "all_india"}]),
                         ("Kerala project", [{"level": "state", "state": code(p, "Kerala")}])]:
        r = ngo.post("/api/ngo/projects", json=project(p, title, areas))
        assert r.status_code == 200, r.text
    items = comp.get("/api/company/projects").json()["items"]
    mine = ["District project", "Statewide project", "Pan India project", "Kerala project"]
    ranked = [i for i in items if i["title"] in mine]  # the demo seed has other projects too
    assert [i["title"] for i in ranked] == mine
    assert [i["fit"]["area_level"] for i in ranked] == ["district", "state", "all_india", "none"]


def test_bad_areas_are_rejected(env):
    p = env
    ngo = login(SHIKSHA)
    raj = code(p, "Rajasthan")
    kochi = district(p, "Kerala", "Ernakulam")
    r = ngo.post("/api/ngo/projects", json=project(p, "Wrong", [{"level": "district", "state": raj, "district": kochi}]))
    assert r.status_code == 422 and "not in Rajasthan" in r.json()["detail"]
    merged = next(s for s in p.geo.states(include_inactive=True) if s["status"] == "merged")
    r = ngo.post("/api/ngo/projects", json=project(p, "Old UT", [{"level": "state", "state": merged["lgd_code"]}]))
    assert r.status_code == 422  # legacy codes can't be chosen for new data
    r = ngo.post("/api/ngo/projects", json=project(p, "No place", []))
    assert r.status_code == 422


# -- RFF, proposals, acceptance, project management, notifications ------------------------------------------------
def test_request_for_funds_acceptance_and_project_management(env):
    p = env
    raj = code(p, "Rajasthan")
    ngo, comp = login(SHIKSHA), login(THAR)
    prj = ngo.post("/api/ngo/projects", json=project(p, "Library corners", [{"level": "state", "state": raj}])).json()
    thar = next(c for c in ngo.get("/api/ngo/companies").json() if c["name"].startswith("Thar"))
    req = ngo.post(f"/api/ngo/projects/{prj['id']}/request-funds",
                   json={"company_id": thar["company_id"], "amount_inr": 800000, "message": "Phase 1"}).json()
    assert req["status"] == "pending" and req["kind"] == "rff"
    again = ngo.post(f"/api/ngo/projects/{prj['id']}/request-funds", json={"company_id": thar["company_id"]})
    assert again.status_code == 409  # one pending request per project and company

    n = comp.get("/api/notifications").json()
    assert n["unread"] >= 1 and "request for funds" in n["items"][0]["text"]
    assert login(MARUDHAR).get("/api/company/requests").json() == []  # other companies never see it
    assert not any(e["id"] == req["id"] for e in comp.get("/api/engagements").json())  # pending: not managed yet

    dec = comp.post(f"/api/company/requests/{req['id']}/decide", json={"accept": True, "note": "Welcome"}).json()
    assert dec["status"] == "accepted"
    assert "accepted" in ngo.get("/api/notifications").json()["items"][0]["text"]
    assert comp.post(f"/api/company/requests/{req['id']}/decide", json={"accept": False}).status_code == 409

    e = comp.post(f"/api/engagements/{req['id']}/updates",
                  json={"kind": "milestone", "title": "Set up 4 libraries", "due_date": "2027-03-31"}).json()
    mid = e["updates"][-1]["id"]
    assert comp.post(f"/api/engagements/{req['id']}/updates", json={"kind": "progress", "title": "x", "percent": 10}).status_code == 422
    ngo.post(f"/api/engagements/{req['id']}/updates", json={"kind": "progress", "title": "Two libraries open", "percent": 50})
    e = ngo.post(f"/api/engagements/{req['id']}/updates",
                 json={"kind": "utilisation", "title": "Books bought", "amount_inr": 120000}).json()
    assert e["summary"]["latest_progress"] == 50 and e["summary"]["utilised_inr"] == 120000
    e = ngo.post(f"/api/engagements/{req['id']}/milestones/{mid}", json={"done": True}).json()
    assert e["summary"]["milestones_done"] == 1
    assert ngo.post(f"/api/engagements/{req['id']}/complete").status_code == 403  # only the company closes it
    done = comp.post(f"/api/engagements/{req['id']}/complete").json()
    assert done["status"] == "completed"
    assert ngo.post(f"/api/engagements/{req['id']}/updates", json={"kind": "note", "title": "late"}).status_code == 409
    ngo.post("/api/notifications/read")
    assert ngo.get("/api/notifications").json()["unread"] == 0


def test_calls_and_proposals(env):
    p = env
    raj, guj = code(p, "Rajasthan"), code(p, "Gujarat")
    comp, ngo = login(THAR), login(SHIKSHA)
    bad = comp.post("/api/company/calls", json={"title": "Old", "sectors": ["education"], "description": "x" * 30,
                                                "deadline": "2001-01-01", "areas": [{"level": "state", "state": raj}]})
    assert bad.status_code == 422
    call = comp.post("/api/company/calls", json={
        "title": "School libraries in western India", "sectors": ["education"], "budget_min": 500000, "budget_max": 2000000,
        "description": "We invite proposals for school library programmes.", "deadline": "2099-12-31",
        "areas": [{"level": "state", "state": raj}, {"level": "state", "state": guj}]}).json()
    calls = ngo.get("/api/ngo/calls").json()["items"]
    mine = next(c for c in calls if c["id"] == call["id"])
    assert mine["fit"]["components"]["sector_fit"] == 1.0 and "Same state" in mine["fit"]["reasons"]
    prj = ngo.post("/api/ngo/projects", json=project(p, "Reading circles", [{"level": "state", "state": raj}])).json()
    prop = ngo.post(f"/api/ngo/calls/{call['id']}/proposals", json={"project_id": prj["id"], "amount_inr": 900000}).json()
    assert prop["kind"] == "proposal" and prop["call_title"] == call["title"]
    assert comp.get("/api/company/calls").json()[0]["proposals"] == 1
    assert ngo.post(f"/api/ngo/requests/{prop['id']}/withdraw").json()["status"] == "withdrawn"
    comp.post(f"/api/company/calls/{call['id']}/close")
    assert not any(c["id"] == call["id"] for c in ngo.get("/api/ngo/calls").json()["items"])
    assert ngo.post(f"/api/ngo/calls/{call['id']}/proposals", json={"project_id": prj["id"]}).status_code == 409


def test_feedback_open_to_everyone(env):
    anon = TestClient(app).post("/api/feedback", json={"category": "idea", "message": "Add Tamil please", "language": "ta"})
    assert anon.status_code == 200
    assert login(HARA).post("/api/feedback", json={"category": "bug", "message": "x"}).status_code == 422  # too short
    row = env.db.one("SELECT * FROM feedback WHERE message = 'Add Tamil please'")
    assert row["user_id"] is None and row["language"] == "ta"


# -- 5. thin coverage + fallback + relative scores ------------------------------------------------------------
def test_thin_area_gets_warning_and_labelled_lower_weight_fallback(env):
    from fundbridge.models import NGOProfile
    from fundbridge.pipeline import _with_relative, coverage_and_fallback

    csr = STATE["backends"].csr
    poor = next(s["name"] for s in env.geo.states() if csr.coverage(s["lgd_code"], ["education"])["level"] == "NONE")
    ngo = NGOProfile(ngo_id="t", name="t", cause="education", state=poor, district="", funding_ask_inr=100000)
    out = coverage_and_fallback(csr, ngo, ["education"], 5)
    assert out["coverage"]["level"] == "NONE" and "weak evidence" in out["coverage"]["warning"]
    fb = out["fallback"]
    assert "not local matches" in fb["pan_india"]["label"] and "not local matches" in fb["similar_states"]["label"]
    for f in fb["pan_india"]["funders"]:
        assert f["weight"] < 1 and f["fallback_score"] <= 100 * f["weight"] and f["reason"]
    rich = max(env.geo.states(), key=lambda s: csr.coverage(s["lgd_code"], ["education"])["state"]["n_companies"])
    good = coverage_and_fallback(csr, NGOProfile(ngo_id="t", name="t", cause="education", state=rich["name"],
                                                 funding_ask_inr=100000), ["education"], 5)
    assert "fallback" not in good or good.get("fallback") is None or good["coverage"]["level"] != "GOOD"
    ranked = _with_relative([{"score": {"total": 40.0}}, {"score": {"total": 20.0}}])
    assert [r["score"]["relative"] for r in ranked] == [100.0, 50.0] and ranked[0]["score"]["total"] == 40.0


def test_run_records_coverage_and_geo_version(env):
    r = login(SHIKSHA).post("/api/run", json={"cause": "education", "state": "Rajasthan", "district": "Barmer",
                                              "funding_ask_inr": 1500000, "sectors": ["education"]}).json()
    assert r["coverage"]["level"] in ("GOOD", "THIN", "NONE") and r["geo_version"] == env.geo.version
    assert all("relative" in x["score"] for x in r["ranked"])
    assert max(x["score"]["relative"] for x in r["ranked"]) == 100.0


# -- 8. admin scoping -------------------------------------------------------------------------------------
def test_state_scoped_admin_sees_only_its_states(env):
    p = env
    import uuid

    raj, guj = code(p, "Rajasthan"), code(p, "Gujarat")
    tag = uuid.uuid4().hex[:6]  # the test geography DB is shared between runs
    rj_name, gj_name = f"Barmerr {tag}", f"Kuchh {tag}"
    p.geo.record_unmapped(rj_name, "district", raj, context="test")
    p.geo.record_unmapped(gj_name, "district", guj, context="test")
    p.admin.create("rj@admin.example", "longpassword", states=[raj], languages=["hi"])
    p.admin.create("all@admin.example", "longpassword")
    rj, nat = login("rj@admin.example", "longpassword"), login("all@admin.example", "longpassword")
    assert rj.get("/api/admin/scope").json()["state_names"] == ["Rajasthan"]
    mine = [i for i in rj.get("/api/admin/geo/unmapped").json() if tag in i["raw"]]
    assert {i["raw"] for i in mine} == {rj_name}
    assert all(i["state_lgd_code"] == raj for i in rj.get("/api/admin/geo/unmapped").json())
    assert rj.get(f"/api/admin/geo/unmapped?state={guj}").status_code == 403
    guj_item = next(i for i in nat.get("/api/admin/geo/unmapped").json() if i["raw"] == gj_name)
    assert rj.post(f"/api/admin/geo/unmapped/{guj_item['id']}", json={"target_code": None}).status_code == 403
    barmer = district(p, "Rajasthan", "Barmer")
    item = mine[0]
    assert rj.post(f"/api/admin/geo/unmapped/{item['id']}", json={"target_code": barmer}).status_code == 200
    from fundbridge.geo import Geo

    assert Geo(p.geo.db_path).resolve_district(rj_name, raj).code == barmer  # the admin's decision is now an alias
    assert rj.put("/api/admin/sectors/education", json={"items": ["(ii)"]}).status_code == 403  # national only
    assert rj.put(f"/api/admin/state-portals/{guj}", json={"url": "https://x.example/", "note": "confirmed somewhere"}).status_code == 403
    assert rj.put(f"/api/admin/state-portals/{raj}", json={"url": "http://insecure.example/"}).status_code == 422
    assert login(SHIKSHA).get("/api/admin/geo/unmapped").status_code == 403
    TestClient(app).post("/api/feedback", json={"message": "Rajasthan feedback", "state": raj, "language": "hi"})
    TestClient(app).post("/api/feedback", json={"message": "Gujarat feedback", "state": guj, "language": "gu"})
    TestClient(app).post("/api/feedback", json={"message": "Rajasthan in Tamil", "state": raj, "language": "ta"})
    assert [f["message"] for f in rj.get("/api/admin/feedback").json()] == ["Rajasthan feedback"]
    assert len(nat.get("/api/admin/feedback").json()) >= 2


def test_dataset_version_without_coverage_tables_degrades_to_unknown(env, monkeypatch):
    import sqlite3

    from fundbridge.models import NGOProfile
    from fundbridge.pipeline import coverage_and_fallback

    csr = STATE["backends"].csr

    def old_version(*a, **k):
        raise sqlite3.OperationalError("no such table: agg_state_sector_company")

    monkeypatch.setattr(csr, "coverage", old_version)
    out = coverage_and_fallback(csr, NGOProfile(ngo_id="t", name="t", cause="education", state="Rajasthan",
                                                funding_ask_inr=1), ["education"], 5)
    assert out["coverage"]["level"] == "UNKNOWN" and "rebuild" in out["coverage"]["warning"]
