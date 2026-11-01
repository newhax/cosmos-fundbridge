"""API smoke test of the NGO email-outreach flow (logged in; ids come from the session)."""
import pytest
from fastapi.testclient import TestClient
from platform_fixtures import SHIKSHA, install, login

from fundbridge.api import app
from fundbridge.config import DATASETS_DIR

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")


@pytest.fixture()
def client(tmp_path):
    with TestClient(app):
        install(tmp_path)
        yield login(SHIKSHA)


def test_full_flow(client):
    st = client.get("/api/status").json()
    assert st["data_source"] == "SAMPLE" and st["dataset"]["data_source"] == "SAMPLE" and st["dataset"]["version"] >= 1

    run = client.post("/api/run", json={"cause": "education", "state": "Rajasthan", "district": "Barmer",
                                        "funding_ask_inr": 1500000}).json()
    assert run["ngo_id"] == "shiksha-setu-demo"  # from the session, not the request
    top = run["ranked"][0]
    rows = client.post("/api/rows", json={"refs": top["sources"][:3]}).json()
    assert len(rows) == 3 and rows[0]["source_ref"] in top["sources"]

    pkg = client.post("/api/outreach", json={"company_id": top["company_id"]}).json()
    assert len(pkg["drafts"]) == 2
    d = pkg["drafts"][0]
    assert client.post(f"/api/outreach/{top['company_id']}/sent", json={**d, "approved": False}).status_code == 400
    assert client.post(f"/api/outreach/{top['company_id']}/sent", json={**d, "approved": True}).status_code == 200

    evs = client.post(f"/api/followups/{top['company_id']}/events",
                      json={"type": "reply_received", "text": "We will review your proposal by 30 December 2026."}).json()
    assert [e["type"] for e in evs] == ["reply_received", "promise_made"]

    fu = client.get("/api/followups").json()
    comp = next(c for c in fu["companies"] if c["key"] == top["company_id"])
    assert [e["type"] for e in comp["events"]] == ["drafted", "sent", "reply_received", "promise_made"]
    assert comp["next_step"]["action"] == "wait" and comp["next_step"]["due"] == "2026-12-30"
    assert any(c["channel"] == "in_app" for c in fu["companies"])  # seeded pitch thread shows up too


def test_top_spenders_is_public_and_sorted(tmp_path):
    with TestClient(app) as c:
        out = c.get("/api/top-spenders").json()  # no login needed: public filing totals only
    names = out["companies"]
    assert out["data_source"] == "SAMPLE" and 0 < len(names) <= 24
    assert set(names[0]) == {"company_name", "spend_inr"}
    spend = [x["spend_inr"] for x in names]
    assert spend == sorted(spend, reverse=True)


def test_dataset_commands_list_activate_and_rollback(tmp_path, capsys):
    import ingest

    from fundbridge import datasets
    from fundbridge.config import SAMPLE_DIR

    root = tmp_path / "datasets"  # never touch the shared test dataset
    files = sorted(SAMPLE_DIR.glob("*.csv"))
    datasets.activate(datasets.build_version(files, "SAMPLE", root=root)["version"], root)
    datasets.build_version(files[:1], "SAMPLE", root=root)  # v2, not active
    ok = lambda: None  # noqa: E731 - the store health check is covered in test_datasets

    ingest.list_versions(root)
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out and out.splitlines()[0].endswith("* active")
    ingest.switch(2, root, ok)
    assert datasets.active_pointer(root)["version"] == 2 and "Active: csr-filings v2" in capsys.readouterr().out
    ingest.switch(2, root, ok)
    assert "already active" in capsys.readouterr().out
    ingest.switch(None, root, ok)  # rollback
    assert datasets.active_pointer(root)["version"] == 1
    with pytest.raises(SystemExit, match="Not switched"):
        ingest.switch(7, root, ok)
    assert datasets.active_pointer(root)["version"] == 1
