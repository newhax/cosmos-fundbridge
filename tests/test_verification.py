"""Verification (automatic checks only), no admin role, and reports that only affect the reporter."""
import pytest
from fastapi.testclient import TestClient
from platform_fixtures import ANANYA_EMAIL as ANANYA, HARA, MARUDHAR, SHIKSHA, THAR, install, login

from fundbridge.api import app
from fundbridge.config import DATASETS_DIR
from fundbridge.platform.verification import valid_cin, valid_darpan, valid_pan

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")


@pytest.fixture()
def env(tmp_path):
    with TestClient(app):
        yield install(tmp_path)


def cid(platform, email):
    return platform.db.one("SELECT c.company_id FROM company_profiles c JOIN users u ON u.id = c.user_id WHERE u.email = ?",
                           (email,))["company_id"]


def ngo_id(platform, email):
    return platform.db.one("SELECT n.ngo_id FROM ngo_profiles n JOIN users u ON u.id = n.user_id WHERE u.email = ?",
                           (email,))["ngo_id"]


@pytest.mark.parametrize("pan,ok", [("AAACT1234Q", True), ("ABCPE1234F", True), ("aaact1234q", True),
                                    ("AAAXT1234Q", False), ("AAACT12345", False), ("AAAC1234Q", False), ("", False)])
def test_pan_regex(pan, ok):
    assert valid_pan(pan) is ok


@pytest.mark.parametrize("cin,ok", [("L17110MH1973PLC019786", True), ("U72200KA2001PTC028790", True),
                                    ("U85300DL2015NPL123456", True), ("X17110MH1973PLC019786", False),
                                    ("L17110MH1973XYZ019786", False), ("L17110MH73PLC019786", False), ("SYN0000000000001", False)])
def test_cin_regex(cin, ok):
    assert valid_cin(cin) is ok


def test_darpan_regex():
    assert valid_darpan("RJ/2019/0234567") and not valid_darpan("RJ-2019-0234567")


def _all_text(obj) -> str:
    return str(obj).lower()


def test_verified_is_never_shown_anywhere(env):
    platform = env
    v = platform.verification
    for t, eid in [("company", cid(platform, THAR)), ("company", cid(platform, MARUDHAR)),
                   ("ngo", ngo_id(platform, SHIKSHA)), ("ngo", ngo_id(platform, HARA))]:
        st = v.status(t, eid)
        assert st["level"] in ("self_declared", "format_checked", "evidence_linked")
        assert "verified" not in _all_text(st["badge"])
    for n in login(THAR).get("/api/company/directory").json():
        assert "verified" not in _all_text(n)
    for r in login(ANANYA).get("/api/donor/discover").json()["recommendations"]:
        assert "verified" not in _all_text(r["ngo"])


def test_admins_cannot_sign_up_and_old_admin_powers_stay_removed(env):
    platform = env
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "boss@x.example", "password": "longpassword", "role": "admin",
                                         "name": "Boss", "consent": True})
    assert r.status_code == 422  # admins are created only by scripts/admin.py
    for path in ("/api/admin/companies", "/api/admin/verification/queue", "/api/admin/datasets"):
        assert login(THAR).get(path).status_code in (403, 404, 405)  # no approval or manual verification any more
    # A scoped admin created internally survives a restart.
    platform.admin.create("ops@x.example", "longpassword", states=[], languages=[])
    from fundbridge.platform.db import PlatformDB
    PlatformDB(platform.db.path)
    assert platform.db.one("SELECT 1 AS x FROM users WHERE role = 'admin' AND email = 'ops@x.example'")


def test_new_company_can_use_the_app_immediately(env):
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "csr@newco.example", "password": "longpassword", "role": "company",
                                         "name": "NewCo Ltd", "consent": True})
    assert r.status_code == 200, r.text
    assert c.get("/api/company/inbox").status_code == 200
    assert c.get("/api/company/directory").status_code == 200
    assert c.get("/api/company/pipeline").status_code == 200
    assert "approval_status" not in c.get("/api/auth/me").json()


def test_reports_hide_only_for_the_reporter(env):
    platform = env
    shiksha = ngo_id(platform, SHIKSHA)
    # A donor report hides the NGO from that donor only.
    assert any(r["ngo"]["ngo_id"] == shiksha for r in login(ANANYA).get("/api/donor/discover").json()["recommendations"])
    r = login(ANANYA).post(f"/api/donor/report/{shiksha}", json={"reason": "suspicious claims"})
    assert r.status_code == 200 and "admin" not in r.json()["message"].lower()
    assert not any(r["ngo"]["ngo_id"] == shiksha for r in login(ANANYA).get("/api/donor/discover").json()["recommendations"])
    assert any(n["ngo_id"] == shiksha for n in login(THAR).get("/api/company/directory").json())  # others unaffected
    assert any(n["ngo_id"] == shiksha for n in login(MARUDHAR).get("/api/company/directory").json())
    # A company report blocks the NGO and hides it from that company's directory only.
    thar = login(THAR)
    pitch = thar.get("/api/company/inbox").json()[0]
    assert thar.post(f"/api/company/pitches/{pitch['id']}/report", json={"reason": "unsolicited"}).status_code == 200
    assert not any(n["ngo_id"] == pitch["ngo_id"] for n in thar.get("/api/company/directory").json())
    assert any(n["ngo_id"] == pitch["ngo_id"] for n in login(MARUDHAR).get("/api/company/directory").json())
    assert platform.verification.status("ngo", pitch["ngo_id"])["level"] in ("self_declared", "format_checked")


def test_company_with_unknown_cin_stays_self_declared(env):
    platform = env
    c = TestClient(app)
    c.post("/api/auth/signup", json={"email": "x@corp.example", "password": "longpassword", "role": "company",
                                     "name": "Unknown Corp", "consent": True})
    c.put("/api/company/profile", json={"cin": "U72200KA2001PTC028790"})  # valid format, not in the CSR filings
    st = c.get("/api/verification/me").json()
    assert st["level"] == "self_declared"
    assert any("not found in the ingested CSR filings" in a["detail"] for a in st["auto_checks"])
    # a company whose CIN joins to filings with a matching name is evidence_linked (not "verified")
    m = platform.verification.status("company", cid(platform, MARUDHAR))
    assert m["level"] == "evidence_linked" and "verified" not in _all_text(m["badge"])
    assert any(a["check"] == "csr_filings" and a["ok"] and a.get("source_refs") for a in m["auto_checks"])


def test_duplicate_ids_and_name_mismatch_are_detected(env):
    platform = env
    hara = ngo_id(platform, HARA)
    login(HARA).put("/api/ngo/profile", json={"darpan_id": "RJ/2019/0234567"})  # Shiksha's Darpan ID
    st = platform.verification.status("ngo", hara)
    assert st["level"] == "self_declared" and any("also used by another account" in w for w in st["warnings"])
    login(MARUDHAR).put("/api/company/profile", json={"name": "Totally Different Ltd"})
    st = platform.verification.status("company", cid(platform, MARUDHAR))
    assert st["level"] != "evidence_linked"
    assert any(a["check"] == "name_consistency" and not a["ok"] for a in st["auto_checks"])
