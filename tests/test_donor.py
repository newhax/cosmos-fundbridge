"""Donor role: guards, opt-in discovery, deterministic matcher, no-payment Give, memory recall, anonymity."""
import socket

import pytest
from fastapi.testclient import TestClient
from platform_fixtures import HARA, SHIKSHA, THAR, install, login

from fundbridge.agents.matcher import score
from fundbridge.api import app
from fundbridge.config import DATASETS_DIR
from fundbridge.state import STATE

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")
ANANYA = "ananya@donor.demo.fundbridge.local"
ROHAN = "rohan@donor.demo.fundbridge.local"


@pytest.fixture()
def env(tmp_path):
    with TestClient(app):
        yield install(tmp_path), tmp_path


@pytest.mark.parametrize("method,path", [
    ("get", "/api/ngo/pitches"), ("get", "/api/ngo/profile"), ("get", "/api/ngo/supporters"), ("get", "/api/followups"),
    ("get", "/api/runs/latest"), ("get", "/api/company/inbox"), ("get", "/api/company/directory"),
    ("get", "/api/company/footprint"),
])
def test_donor_cannot_access_ngo_or_company_routes(env, method, path):
    assert getattr(login(ANANYA), method)(path).status_code == 403


def test_other_roles_cannot_access_donor_routes_and_companies_never_see_donor_data(env):
    for who in (SHIKSHA, THAR):
        c = login(who)
        for path in ("/api/donor/profile", "/api/donor/discover", "/api/donor/gifts", "/api/donor/memory"):
            assert c.get(path).status_code == 403
    thar = login(THAR)
    blob = str(thar.get("/api/company/directory").json()) + str(thar.get("/api/company/inbox").json())
    assert "Ananya" not in blob and "follower" not in blob and "gift" not in blob.lower()


def test_discovery_hides_ngos_that_did_not_opt_in(env):
    platform, _ = env
    names = [r["ngo"]["name"] for r in login(ANANYA).get("/api/donor/discover").json()["recommendations"]]
    assert "Sakhi Collective (DEMO)" not in names  # accepts donations but not listed publicly
    assert {"Shiksha Setu Foundation (DEMO)", "Hara Bhara Trust (DEMO)"} <= set(names)
    login(HARA).put("/api/ngo/profile", json={"accepts_individual_donations": False})
    names = [r["ngo"]["name"] for r in login(ANANYA).get("/api/donor/discover").json()["recommendations"]]
    assert "Hara Bhara Trust (DEMO)" not in names
    assert login(ANANYA).get("/api/donor/give/" + platform.db.one(
        "SELECT ngo_id FROM ngo_profiles WHERE name LIKE 'Sakhi%'")["ngo_id"]).status_code == 404


def test_matcher_is_deterministic_and_ignores_wording(env):
    donor = {"causes": ["education"], "state": "Rajasthan", "city": "Barmer", "giving_range": "1000_5000"}
    ngo = {"ngo_id": "n", "name": "N", "causes": ["education", "health"], "state": "Rajasthan", "district": "Barmer",
           "suggested_gift_inr": 2000}
    a = score(donor, ngo | {"summary": "We deliver transformative, evidence-based learning outcomes at scale."})
    b = score(donor, ngo | {"summary": "we help kids read. pls support"})
    assert a == b and a["total"] == 75.0  # 0.5*0.5 + 0.3*1.0 + 0.2*1.0
    c = login(ANANYA)
    first = [(r["ngo"]["ngo_id"], r["score"]["total"]) for r in c.get("/api/donor/discover").json()["recommendations"]]
    second = [(r["ngo"]["ngo_id"], r["score"]["total"]) for r in c.get("/api/donor/discover").json()["recommendations"]]
    assert first == second
    rec = c.get("/api/donor/discover").json()["recommendations"][0]
    assert rec["trace"]["components"] and rec["trace"]["weights"]
    assert all(p["status"] in ("self-declared", "unknown") for p in rec["ngo"]["credentials_panel"])
    assert "verified" not in str(rec["ngo"]["credentials_panel"]).lower()


def test_give_never_calls_a_payment_api_and_url_must_be_https(env, monkeypatch):
    platform, _ = env
    shiksha = "shiksha-setu-demo"
    c = login(ANANYA)

    def no_network(*a, **k):
        raise AssertionError("Give must not make any outbound connection")

    # Block every way an outbound HTTP(S)/payment call could be made (the in-process test client is unaffected).
    import http.client
    import urllib.request
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    calls_before = STATE["llm"].store.used_today("gemini")["calls"]
    r = c.get(f"/api/donor/give/{shiksha}").json()
    assert STATE["llm"].store.used_today("gemini")["calls"] == calls_before  # no model call either
    assert r["url"].startswith("https://") and "80G" in r["notice"] and "not tax advice" in r["notice"]
    monkeypatch.undo()
    ngo = login(SHIKSHA)
    for bad in ("http://example.org/give", "javascript:alert(1)", "https://localhost/give", "https://127.0.0.1/x",
                "https://user:pw@example.org/x", "ftp://example.org"):
        assert ngo.put("/api/ngo/profile", json={"donation_url": bad}).status_code == 422, bad
    import fundbridge.platform.donors as donors
    assert "payment" not in open(donors.__file__, encoding="utf-8").read().lower().replace("no payment", "").replace(
        "payment, receipt", "").replace("process or see payments", "")


def test_donor_preferences_recalled_after_restart_and_cited(env):
    platform, tmp_path = env
    c = login(ROHAN)
    c.put("/api/donor/profile", json={"causes": ["education"], "city": "Barmer", "state": "Rajasthan"})
    install(tmp_path, seed=False)  # restart: fresh singletons on the same files
    c = login(ROHAN)
    mem = c.get("/api/donor/memory").json()
    assert any(m["type"] == "preferences" and "education" in m["summary"] for m in mem)
    out = c.get("/api/donor/discover").json()
    assert out["memory_recalled"] > 0
    assert any("preferences" in u["text"] for u in out["recommendations"][0]["memory_used"])
    assert out["recommendations"][0]["ngo"]["name"].startswith("Shiksha")  # new preferences drive the ranking


def test_donor_identity_hidden_unless_opted_in(env):
    platform, _ = env
    shiksha = login(SHIKSHA).get("/api/ngo/supporters").json()
    assert shiksha["named_followers"] == ["Ananya (DEMO)"]  # Ananya is not anonymous by default
    hara = login(HARA).get("/api/ngo/supporters").json()
    assert hara["followers"] == 1 and hara["named_followers"] == []  # Rohan is anonymous by default
    rohan = login(ROHAN)
    hara_id = platform.db.one("SELECT ngo_id FROM ngo_profiles WHERE name LIKE 'Hara%'")["ngo_id"]
    rohan.post("/api/donor/gifts", json={"ngo_id": hara_id, "amount_inr": 500, "date": "2026-10-01"})
    gifts = login(HARA).get("/api/ngo/supporters").json()["gifts"]
    assert gifts[0]["donor"] == "Anonymous donor" and gifts[0]["status"] == "self-reported"
    login(HARA).post(f"/api/ngo/gifts/{gifts[0]['id']}/confirm")
    assert rohan.get("/api/donor/gifts").json()[0]["status"] == "confirmed by NGO"
    rohan.post("/api/donor/gifts", json={"ngo_id": hara_id, "amount_inr": 700, "date": "2026-10-02", "share_name": True})
    assert "Rohan (DEMO)" in [g["donor"] for g in login(HARA).get("/api/ngo/supporters").json()["gifts"]]


def test_donors_never_see_each_other_and_reports_are_recorded(env):
    platform, _ = env
    assert all(g["ngo_name"].startswith("Shiksha") for g in login(ANANYA).get("/api/donor/gifts").json())
    assert login(ROHAN).get("/api/donor/gifts").json() == []  # Ananya's seeded gift is invisible to Rohan
    assert login(ROHAN).post("/api/donor/report/shiksha-setu-demo", json={"reason": "misleading claims"}).status_code == 200
    assert len(platform.db.all("SELECT * FROM ngo_reports")) == 1


def test_feed_labels_updates_and_gift_validation(env):
    c = login(ANANYA)
    feed = c.get("/api/donor/feed").json()
    assert feed and all(u["label"] == "reported by the NGO" for u in feed)
    assert c.post("/api/donor/gifts", json={"ngo_id": "shiksha-setu-demo", "amount_inr": 100, "date": "2999-01-01"}).status_code == 422
    assert c.put("/api/donor/profile", json={"state": "California"}).status_code == 422  # India only


def test_update_draft_uses_gemini_route_and_posting_needs_approval(env):
    s = STATE["llm"].settings
    assert all(p == "gemini" for p, _ in s.routes("update_draft")) and all(p == "gemini" for p, _ in s.routes("matcher"))
    ngo = login(SHIKSHA)
    d = ngo.post("/api/ngo/updates/draft", json={"facts": "Started 2 reading circles in Barmer this month."}).json()
    assert d["body"]  # offline: the NGO's own notes, nothing invented
    assert ngo.post("/api/ngo/updates", json={"title": d["title"], "body": d["body"], "approved": False}).status_code == 400
    assert ngo.post("/api/ngo/updates", json={"title": d["title"], "body": d["body"], "approved": True}).status_code == 200


def test_matcher_explanation_strips_fact_ids():
    from types import SimpleNamespace

    from fundbridge.agents.matcher import Why, Whys, recommend

    class FakeSession:
        enabled = True

        def call(self, role, system, user, schema):
            assert role == "matcher"
            return SimpleNamespace(output=Whys(items=[Why(ngo_id="n1", text="[F1] Shared causes: education. [F2] N works in Barmer, Rajasthan.")]),
                                   meta=SimpleNamespace(produced_by="gemini:fake"))

        def call_log(self):
            return []

    donor = {"causes": ["education"], "state": "Rajasthan", "city": "Barmer", "giving_range": ""}
    ngo = {"ngo_id": "n1", "name": "N", "causes": ["education"], "state": "Rajasthan", "district": "Barmer"}
    rec = recommend(FakeSession(), donor, [ngo], [])[0]
    assert rec["why_mode"] == "llm" and "[F" not in rec["why"]
